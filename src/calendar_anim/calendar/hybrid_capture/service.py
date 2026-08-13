import json
import statistics
from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager, suppress
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Protocol

from PIL import Image

from calendar_anim.calendar.frame_mapping.models import SingleFrameCalendarPlan
from calendar_anim.calendar.hybrid_capture.artifacts import (
    LEGACY_RESOLUTION,
    AccountBSingleCaptureStore,
    HybridCaptureStore,
    compare_logical_cells,
    compose_final_sanity_contact_sheet,
    compose_high_resolution_comparison,
    compose_mode_contact_sheet,
    compose_output_mode,
    compose_sanity_contact_sheet,
    compose_seam_geometry,
    expected_distribution,
    normalize_grid,
    render_expected_frame,
    write_atomic,
)
from calendar_anim.calendar.hybrid_capture.models import (
    CURRENT_CAPTURE_IMPLEMENTATION_VERSION,
    CURRENT_PROFILE_NAVIGATION_VERSION,
    FINAL_SANITY_SCHEMA_VERSION,
    FinalHybridSanityReport,
    FinalSanityFrameResult,
    HybridCapturePlan,
    HybridCaptureState,
    HybridFramePlan,
    HybridFrameStatus,
    HybridOutputMode,
    HybridSanityReport,
    HybridSeamReport,
    SanityFrameResult,
    SingleProfilePreviewFrameResult,
    SingleProfilePreviewReport,
)
from calendar_anim.exceptions import CalendarAnimError


class HybridBrowserGateway(Protocol):
    def open_week(self, week_start: date) -> None: ...

    def wait_until_ready(self, week_start: date, minimum_event_count: int) -> None: ...

    def capture_viewport(self, output_path: Path) -> None: ...

    def capture_logical_event_grid(self, output_path: Path) -> dict[str, object]: ...

    def capture_header_event_grid(self, output_path: Path) -> dict[str, object]: ...

    def wait_for_animation_events(self, expected_count: int) -> object: ...

    def reload_current_week(self, week_start: date, minimum_event_count: int) -> None: ...

    def capture_debug_state(self) -> dict[str, object]: ...

    def inspect_navigation(self, expected_week: date) -> dict[str, object]: ...


class CaptureLoadFailure(CalendarAnimError):
    def __init__(
        self,
        errors: list[str],
        diagnostic_paths: list[Path],
        diagnostic_json_paths: list[Path],
        population_samples: list[dict[str, object]],
        context: dict[str, object] | None = None,
        last_browser_state: dict[str, object] | None = None,
    ) -> None:
        details = context or {}
        navigation = (
            last_browser_state.get("navigation", {}) if isinstance(last_browser_state, dict) else {}
        )
        if not isinstance(navigation, dict):
            navigation = {}
        suffix = (
            f" profile={details.get('profile')}, human_frame={details.get('human_frame')},"
            f" frame_index={details.get('frame_index')}, week={details.get('week_start')},"
            f" url={navigation.get('current_url')}, state={navigation.get('state')}"
        )
        super().__init__(
            f"CAPTURE LOAD FAILURE after {len(errors)} attempts:{suffix}: {errors[-1]}"
        )
        self.errors = errors
        self.diagnostic_paths = diagnostic_paths
        self.diagnostic_json_paths = diagnostic_json_paths
        self.population_samples = population_samples
        self.context = details
        self.last_browser_state = last_browser_state or {}


GatewayContextFactory = Callable[[str, int], AbstractContextManager[HybridBrowserGateway]]


class HybridCaptureService:
    def __init__(self, store: HybridCaptureStore, gateway_factory: GatewayContextFactory) -> None:
        self.store = store
        self.gateway_factory = gateway_factory

    def capture_sanity(
        self, plan: HybridCapturePlan, human_frames: Sequence[int]
    ) -> HybridSanityReport:
        selected = [plan.frames[value - 1] for value in human_frames]
        if any(frame.calendar_profile != "account-b" for frame in selected):
            raise CalendarAnimError("Sanity capture is restricted to Account B")
        results: list[SanityFrameResult] = []
        with self.gateway_factory("account-b", 90) as gateway:
            for frame in selected:
                results.append(self._capture_sanity_frame(gateway, plan, frame))
        successful_widths = [
            item.logical_cell_width for item in results if item.capture_load_success
        ]
        successful_heights = [
            item.logical_cell_height for item in results if item.capture_load_success
        ]
        if successful_widths and successful_heights:
            median_width = statistics.median(successful_widths)
            median_height = statistics.median(successful_heights)
            for item in results:
                item.grid_geometry_valid = item.capture_load_success and (
                    _relative_delta(item.logical_cell_width, median_width) <= 0.02
                    and _relative_delta(item.logical_cell_height, median_height) <= 0.02
                )
        if any(not item.capture_load_success for item in results):
            automated = "CAPTURE ERROR"
        else:
            automated = "PASS" if all(_sanity_passes(item) for item in results) else "NO-GO"
        report = HybridSanityReport(
            run_id=plan.run_id,
            frames_checked=list(human_frames),
            results=results,
            automated_result=automated,
        )
        self.store.save_sanity_report(report)
        compose_sanity_contact_sheet(
            report, self.store.sanity_directory(plan.run_id) / "hybrid-sanity-contact-sheet.png"
        )
        return report

    def capture_final_sanity(
        self,
        plan: HybridCapturePlan,
        human_frames: Sequence[int],
        profile: str,
        mode: HybridOutputMode,
        resolution: tuple[int, int],
    ) -> FinalHybridSanityReport:
        """Capture the versioned final configuration using browser reads only."""

        selected = [plan.frames[value - 1] for value in human_frames]
        if any(frame.calendar_profile != profile for frame in selected):
            raise CalendarAnimError(f"Final sanity frames must belong to {profile}")
        if not mode.includes_header:
            raise CalendarAnimError("Final high-resolution sanity requires a header mode")
        results: list[FinalSanityFrameResult] = []
        with self.gateway_factory(profile, selected[0].capture_zoom_percent) as gateway:
            for frame in selected:
                results.append(
                    self._capture_final_sanity_frame(gateway, plan, frame, mode, resolution)
                )
        automated_result = "PASS" if all(item.passed for item in results) else "CAPTURE ERROR"
        report = FinalHybridSanityReport(
            run_id=plan.run_id,
            profile=profile,
            output_mode=mode,
            output_width=resolution[0],
            output_height=resolution[1],
            frames_checked=list(human_frames),
            results=results,
            automated_result=automated_result,
        )
        self.store.save_final_sanity_report(report)
        directory = self.store.final_sanity_directory(plan.run_id, mode, resolution)
        compose_final_sanity_contact_sheet(report, directory / "sanity-contact-sheet.png")
        return report

    def _capture_final_sanity_frame(
        self,
        gateway: HybridBrowserGateway,
        plan: HybridCapturePlan,
        frame: HybridFramePlan,
        mode: HybridOutputMode,
        resolution: tuple[int, int],
    ) -> FinalSanityFrameResult:
        directory = self.store.final_sanity_directory(plan.run_id, mode, resolution)
        frame_directory = directory / "browser-artifacts" / f"frame-{frame.human_frame:03d}"
        raw = frame_directory / "raw-browser.png"
        logical = frame_directory / "logical-grid.png"
        native = frame_directory / "native-header-grid.png"
        output = directory / f"frame-{frame.human_frame:03d}.png"
        metrics_path = frame_directory / "metrics.json"
        try:
            metrics = self._browser_capture(
                gateway,
                frame,
                raw,
                logical,
                header=native,
                debug_directory=frame_directory,
            )
            compose_output_mode(
                logical,
                native,
                output,
                mode,
                resolution,
                native_header_height=_native_header_height(metrics),
                native_time_gutter_width=_native_time_gutter_width(metrics),
            )
            header_bounds = metrics.get("header_grid_bounds")
            logical_clip = metrics.get("logical_clip")
            header_present = isinstance(header_bounds, dict) and _valid_clip(
                header_bounds.get("header_clip")
            )
            gap_absent = bool(metrics.get("empty_pre_06_interval_removed"))
            visible_window_valid = metrics.get("vertical_interval") == "06:00-00:00"
            dimensions = _image_dimensions(output)
            output_dimensions = (dimensions[0], dimensions[1])
            result = FinalSanityFrameResult(
                human_frame=frame.human_frame,
                frame_index=frame.frame_index,
                week_start=frame.week_start,
                profile=frame.calendar_profile,
                capture_completed=True,
                correct_week=bool(metrics.get("navigation_complete")),
                grid_bounds_valid=_valid_clip(logical_clip),
                output_dimensions=output_dimensions,
                output_resolution_valid=output_dimensions == resolution,
                header_present=header_present,
                pre_06_gap_absent=gap_absent,
                visible_window_valid=visible_window_valid,
                visual_output_non_empty=_image_is_non_empty(output),
                output_artifact=str(output),
                native_crop_artifact=str(native),
                raw_browser_artifact=str(raw),
                metrics_artifact=str(metrics_path),
            )
            write_atomic(
                metrics_path,
                json.dumps(
                    {"capture": metrics, "sanity": result.model_dump(mode="json")},
                    indent=2,
                )
                + "\n",
            )
            return result
        except (CalendarAnimError, OSError, ValueError) as error:
            result = FinalSanityFrameResult(
                human_frame=frame.human_frame,
                frame_index=frame.frame_index,
                week_start=frame.week_start,
                profile=frame.calendar_profile,
                capture_completed=False,
                correct_week=False,
                grid_bounds_valid=False,
                output_dimensions=(0, 0),
                output_resolution_valid=False,
                header_present=False,
                pre_06_gap_absent=False,
                visible_window_valid=False,
                visual_output_non_empty=False,
                error=str(error),
                output_artifact=str(output),
                native_crop_artifact=str(native),
                raw_browser_artifact=str(raw),
                metrics_artifact=str(metrics_path),
            )
            write_atomic(metrics_path, result.model_dump_json(indent=2) + "\n")
            return result

    def capture_debug(
        self, plan: HybridCapturePlan, human_frame: int, profile: str
    ) -> dict[str, object]:
        """Capture one frame with exhaustive read-only browser diagnostics."""

        if not 1 <= human_frame <= len(plan.frames):
            raise CalendarAnimError(f"Human frame must be between 1 and {len(plan.frames)}")
        frame = plan.frames[human_frame - 1]
        if frame.calendar_profile != profile:
            raise CalendarAnimError(
                f"Frame {human_frame} belongs to {frame.calendar_profile}, not {profile}"
            )
        directory = self.store.debug_frame_directory(plan.run_id, human_frame)
        raw = directory / "raw-browser.png"
        logical = directory / "grid-crop.png"
        normalized = directory / "normalized.png"
        debug_json = directory / "debug.json"
        try:
            with self.gateway_factory(profile, frame.capture_zoom_percent) as gateway:
                metrics = self._browser_capture(
                    gateway,
                    frame,
                    raw,
                    logical,
                    debug_directory=directory,
                )
            normalize_grid(logical, normalized)
            payload: dict[str, object] = {
                "success": True,
                "run_id": plan.run_id,
                "human_frame": human_frame,
                "frame_index": frame.frame_index,
                "week_start": frame.week_start.isoformat(),
                "profile": profile,
                "expected_occurrences_reference": frame.expected_occurrences,
                "artifacts": {
                    "raw_browser": str(raw),
                    "grid_crop": str(logical),
                    "normalized": str(normalized),
                    "debug_json": str(debug_json),
                },
                "capture": metrics,
                "google_calendar_writes": False,
            }
        except CaptureLoadFailure as error:
            payload = {
                "success": False,
                "run_id": plan.run_id,
                "human_frame": human_frame,
                "frame_index": frame.frame_index,
                "week_start": frame.week_start.isoformat(),
                "profile": profile,
                "expected_occurrences_reference": frame.expected_occurrences,
                "errors": error.errors,
                "debug_screenshots": [str(path) for path in error.diagnostic_paths],
                "debug_attempt_json": [str(path) for path in error.diagnostic_json_paths],
                "google_calendar_writes": False,
            }
            write_atomic(debug_json, json.dumps(payload, indent=2) + "\n")
            raise
        write_atomic(debug_json, json.dumps(payload, indent=2) + "\n")
        return payload

    def capture_debug_modes(
        self,
        plan: HybridCapturePlan,
        human_frame: int,
        profile: str,
        resolution: tuple[int, int] = LEGACY_RESOLUTION,
    ) -> dict[str, object]:
        """Capture once and compose legacy modes or the native high-resolution Mode C."""

        if not 1 <= human_frame <= len(plan.frames):
            raise CalendarAnimError(f"Human frame must be between 1 and {len(plan.frames)}")
        frame = plan.frames[human_frame - 1]
        if frame.calendar_profile != profile:
            raise CalendarAnimError(
                f"Frame {human_frame} belongs to {frame.calendar_profile}, not {profile}"
            )
        high_resolution = resolution != LEGACY_RESOLUTION
        directory = (
            self.store.high_resolution_debug_directory(plan.run_id, human_frame)
            if high_resolution
            else self.store.debug_frame_directory(plan.run_id, human_frame)
        )
        raw = directory / "raw-browser.png"
        logical_source = directory / ".pixel-faithful-source.png"
        header_source = directory / (
            "mode-c-native-crop.png" if high_resolution else ".header-preserved-source.png"
        )
        debug_json = directory / "debug.json"
        mode_paths = _debug_mode_paths(directory, resolution)
        try:
            with self.gateway_factory(profile, frame.capture_zoom_percent) as gateway:
                metrics = self._browser_capture(
                    gateway,
                    frame,
                    raw,
                    logical_source,
                    header=header_source,
                    debug_directory=directory,
                )
            modes: dict[str, object] = {}
            logical_clip = metrics.get("logical_clip")
            header_bounds = metrics.get("header_grid_bounds")
            native_header_height = _native_header_height(metrics)
            for mode, output in mode_paths.items():
                composition = compose_output_mode(
                    logical_source,
                    header_source,
                    output,
                    mode,
                    resolution,
                    native_header_height=native_header_height,
                    native_time_gutter_width=_native_time_gutter_width(metrics),
                )
                composition["bounds"] = (
                    logical_clip if mode is HybridOutputMode.PIXEL_FAITHFUL else header_bounds
                )
                composition["source"] = "native browser crop"
                composition["artifact"] = str(output)
                modes[mode.value] = composition
            comparison = (
                compose_high_resolution_comparison(
                    header_source,
                    mode_paths[HybridOutputMode.HEADER_PRESERVED_FILL],
                    directory / "comparison-hires.png",
                )
                if high_resolution
                else compose_mode_contact_sheet(
                    list(mode_paths.items()), directory / "comparison-contact-sheet.png"
                )
            )
            payload: dict[str, object] = {
                "success": True,
                "run_id": plan.run_id,
                "human_frame": human_frame,
                "frame_index": frame.frame_index,
                "week_start": frame.week_start.isoformat(),
                "profile": profile,
                "capture_zoom_percent": frame.capture_zoom_percent,
                "output_resolution": list(resolution),
                "raw_browser_dimensions": _image_dimensions(raw),
                "native_composed_crop_dimensions": (
                    _image_dimensions(header_source) if high_resolution else None
                ),
                "vertical_interval": "06:00-00:00",
                "logical_grid": [126, 72],
                "pre_06_gap_present": False,
                "bounds_source": "content-independent structural week grid",
                "modes": modes,
                "artifacts": {
                    "raw_browser": str(raw),
                    "native_crop": str(header_source) if high_resolution else None,
                    "final_output": str(mode_paths.get(HybridOutputMode.HEADER_PRESERVED_FILL, "")),
                    "comparison": str(comparison),
                    "debug_json": str(debug_json),
                },
                "capture": metrics,
                "google_calendar_writes": False,
            }
            write_atomic(debug_json, json.dumps(payload, indent=2) + "\n")
            return payload
        except CaptureLoadFailure as error:
            payload = {
                "success": False,
                "run_id": plan.run_id,
                "human_frame": human_frame,
                "frame_index": frame.frame_index,
                "week_start": frame.week_start.isoformat(),
                "profile": profile,
                "errors": error.errors,
                "debug_screenshots": [str(path) for path in error.diagnostic_paths],
                "debug_attempt_json": [str(path) for path in error.diagnostic_json_paths],
                "google_calendar_writes": False,
            }
            write_atomic(debug_json, json.dumps(payload, indent=2) + "\n")
            raise
        finally:
            temporary_paths = [logical_source]
            if not high_resolution:
                temporary_paths.append(header_source)
            for temporary in temporary_paths:
                if temporary.exists():
                    temporary.unlink()

    def capture_final(
        self,
        plan: HybridCapturePlan,
        state: HybridCaptureState,
        mode: HybridOutputMode = HybridOutputMode.PIXEL_FAITHFUL,
        resolution: tuple[int, int] = LEGACY_RESOLUTION,
    ) -> HybridCaptureState:
        if state.output_mode is not mode:
            raise CalendarAnimError("Hybrid capture state does not match selected output mode")
        if (state.output_width, state.output_height) != resolution:
            raise CalendarAnimError("Hybrid capture state does not match selected resolution")
        self.validate_final_capture_gate(plan, mode, resolution)
        self.validate_profile_capture_gates(plan, mode, resolution)
        self._capture_final_profiles(
            plan,
            state,
            mode,
            resolution,
            (("account-a", 33), ("account-b", 90)),
        )
        self._validate_final_sequence(plan, mode, resolution)
        compose_seam_geometry(
            self.store.final_frame_path(plan.run_id, 22, mode, resolution),
            self.store.final_frame_path(plan.run_id, 23, mode, resolution),
            self.store.run_directory(plan.run_id)
            / "seam"
            / mode.directory_name
            / f"{resolution[0]}x{resolution[1]}"
            / "a-b-transition-geometry.png",
        )
        return state

    def capture_final_single_profile(
        self,
        plan: HybridCapturePlan,
        state: HybridCaptureState,
        mode: HybridOutputMode,
        resolution: tuple[int, int],
    ) -> HybridCaptureState:
        if plan.capture_strategy != "single-profile-account-b":
            raise CalendarAnimError("Capture plan is not Account-B single-profile")
        if (
            state.output_mode is not mode
            or (
                state.output_width,
                state.output_height,
            )
            != resolution
        ):
            raise CalendarAnimError("Single-profile capture state differs from selected output")
        if any(
            frame.calendar_profile != "account-b" or frame.capture_zoom_percent != 90
            for frame in plan.frames
        ):
            raise CalendarAnimError("Single-profile capture must use Account B at zoom 90%")
        self._capture_final_profiles(plan, state, mode, resolution, (("account-b", 90),))
        self._validate_final_sequence(plan, mode, resolution)
        return state

    def capture_final_single_profile_preview(
        self,
        plan: HybridCapturePlan,
        human_frames: Sequence[int],
        mode: HybridOutputMode,
        resolution: tuple[int, int],
    ) -> SingleProfilePreviewReport:
        """Capture isolated frames through the exact final composition code, without state."""

        if not isinstance(self.store, AccountBSingleCaptureStore):
            raise CalendarAnimError("Single-profile preview requires isolated preview storage")
        if plan.capture_strategy != "single-profile-account-b":
            raise CalendarAnimError("Preview requires the Account-B single-profile plan")
        if not human_frames or len(human_frames) != len(set(human_frames)):
            raise CalendarAnimError("Preview frames must be non-empty and unique")
        if any(frame < 1 or frame > 108 for frame in human_frames):
            raise CalendarAnimError("Preview frames must be between 1 and 108")
        if mode is not HybridOutputMode.HEADER_PRESERVED_FILL or resolution != (1512, 864):
            raise CalendarAnimError("Preview must match final header_preserved_fill at 1512x864")
        selected = [plan.frames[human_frame - 1] for human_frame in human_frames]
        if any(
            frame.calendar_profile != "account-b" or frame.capture_zoom_percent != 90
            for frame in selected
        ):
            raise CalendarAnimError("Preview may open only Account B at zoom 90%")
        results: list[SingleProfilePreviewFrameResult] = []
        with self.gateway_factory("account-b", 90) as gateway:
            for frame in selected:
                output = self.store.preview_frame_path(plan.run_id, frame.frame_index)
                raw = self.store.preview_component_path(plan.run_id, frame.frame_index, "raw")
                logical = self.store.preview_component_path(
                    plan.run_id, frame.frame_index, "logical"
                )
                header = self.store.preview_component_path(
                    plan.run_id, frame.frame_index, "native-header-grid"
                )
                debug = self.store.preview_debug_directory(plan.run_id, frame.frame_index)
                metrics = self._capture_composed_frame(
                    gateway,
                    frame,
                    raw,
                    logical,
                    header,
                    output,
                    mode,
                    resolution,
                    debug,
                )
                write_atomic(debug / "metrics.json", json.dumps(metrics, indent=2) + "\n")
                results.append(_preview_frame_result(frame, output, metrics, resolution))
        signatures = [_preview_geometry_signature(result) for result in results]
        geometry_consistent = len(set(signatures)) <= 1
        selected_numbers = {result.human_frame for result in results}
        delta = (
            (plan.frames[23].week_start - plan.frames[22].week_start).days
            if {23, 24}.issubset(selected_numbers)
            else None
        )
        report = SingleProfilePreviewReport(
            run_id=plan.run_id,
            frames=results,
            frame_23_to_24_delta_days=delta,
            geometry_consistent=geometry_consistent,
            geometry_warning=(
                None
                if geometry_consistent
                else "Native/source composition geometry varies between preview frames"
            ),
            preview="PASS",
        )
        write_atomic(
            self.store.preview_report_path(plan.run_id),
            report.model_dump_json(indent=2) + "\n",
        )
        write_atomic(
            self.store.preview_report_text_path(plan.run_id),
            _single_profile_preview_text(report),
        )
        return report

    def _capture_final_profiles(
        self,
        plan: HybridCapturePlan,
        state: HybridCaptureState,
        mode: HybridOutputMode,
        resolution: tuple[int, int],
        profiles: tuple[tuple[str, int], ...],
    ) -> None:
        for profile, zoom in profiles:
            frames = [frame for frame in plan.frames if frame.calendar_profile == profile]
            frames = [
                frame
                for frame in frames
                if not (
                    state.frame(frame.frame_index).status is HybridFrameStatus.COMPLETED
                    and self.store.final_frame_path(
                        plan.run_id, frame.frame_index, mode, resolution
                    ).is_file()
                )
            ]
            if not frames:
                continue
            with self.gateway_factory(profile, zoom) as gateway:
                for frame in frames:
                    state_frame = state.frame(frame.frame_index)
                    output = self.store.final_frame_path(
                        plan.run_id, frame.frame_index, mode, resolution
                    )
                    if state_frame.status is HybridFrameStatus.COMPLETED and output.is_file():
                        continue
                    state_frame.status = HybridFrameStatus.CAPTURING
                    state_frame.started_at = datetime.now(UTC)
                    state_frame.error = None
                    self.store.save_state(state)
                    try:
                        raw = self.store.final_raw_path(
                            plan.run_id, frame.frame_index, mode, resolution
                        )
                        logical = self.store.final_logical_path(
                            plan.run_id, frame.frame_index, mode, resolution
                        )
                        header = (
                            self.store.final_header_path(
                                plan.run_id, frame.frame_index, mode, resolution
                            )
                            if mode.includes_header
                            else None
                        )
                        self._capture_composed_frame(
                            gateway,
                            frame,
                            raw,
                            logical,
                            header,
                            output,
                            mode,
                            resolution,
                            self.store.final_capture_failure_directory(
                                plan.run_id, mode, resolution
                            ),
                        )
                    except Exception as error:
                        state_frame.status = HybridFrameStatus.FAILED
                        state_frame.error = str(error)
                        self.store.save_state(state)
                        raise
                    state_frame.status = HybridFrameStatus.COMPLETED
                    state_frame.completed_at = datetime.now(UTC)
                    self.store.save_state(state)

    def check_final_capture_profiles(self, plan: HybridCapturePlan) -> dict[str, object]:
        """Read-only preflight of both persistent profiles and their first target week."""

        selected = (("account-a", 33, plan.frames[0]), ("account-b", 90, plan.frames[23]))
        results: list[dict[str, object]] = []
        directory = self.store.profile_preflight_directory(plan.run_id)
        for profile, zoom, frame in selected:
            result: dict[str, object] = {
                "profile": profile,
                "human_frame": frame.human_frame,
                "frame_index": frame.frame_index,
                "week_start": frame.week_start.isoformat(),
                "zoom_expected": zoom,
                "status": "NOT_RUN",
            }
            try:
                with self.gateway_factory(profile, zoom) as gateway:
                    try:
                        gateway.open_week(frame.week_start)
                        gateway.wait_until_ready(frame.week_start, 0)
                        navigation = gateway.inspect_navigation(frame.week_start)
                        applied_zoom = navigation.get("zoom_applied")
                        zoom_valid = (
                            isinstance(applied_zoom, (int, float))
                            and abs(float(applied_zoom) - zoom) <= 2.0
                        )
                        passed = navigation.get("state") == "ready" and zoom_valid
                        result.update(
                            {
                                "status": "PASS" if passed else "FAIL",
                                "navigation": navigation,
                                "profile_path": navigation.get("browser_profile_path"),
                                "session": (
                                    "logged-in"
                                    if navigation.get("logged_in_detection")
                                    else "not-confirmed"
                                ),
                                "calendar_loaded": navigation.get("calendar_shell_detection"),
                                "week_view_reachable": navigation.get("week_matches"),
                                "zoom_applied": navigation.get("zoom_applied"),
                                "zoom_valid": zoom_valid,
                            }
                        )
                    except Exception as error:
                        basename = (
                            f"capture-failure-{profile}-frame-{frame.human_frame:03d}-attempt-1"
                        )
                        try:
                            browser_state = gateway.capture_debug_state()
                        except Exception as debug_error:
                            browser_state = {"diagnostic_error": str(debug_error)}
                        result["browser"] = browser_state
                        self.store.save_json_report(
                            directory / f"{basename}.json",
                            {**result, "reason": str(error), "browser": browser_state},
                        )
                        with suppress(Exception):
                            gateway.capture_viewport(directory / f"{basename}.png")
                        raise
            except Exception as error:
                result.update({"status": "FAIL", "error": str(error)})
                if bool(getattr(error, "non_retryable", False)):
                    results.append(result)
                    break
            results.append(result)
        checked = {str(item["profile"]) for item in results}
        for profile, zoom, frame in selected:
            if profile not in checked:
                results.append(
                    {
                        "profile": profile,
                        "human_frame": frame.human_frame,
                        "frame_index": frame.frame_index,
                        "week_start": frame.week_start.isoformat(),
                        "zoom_expected": zoom,
                        "status": "NOT_RUN",
                        "reason": "preflight stopped after non-retryable profile failure",
                    }
                )
        report: dict[str, object] = {
            "schema_version": "1.0",
            "profile_navigation_version": CURRENT_PROFILE_NAVIGATION_VERSION,
            "run_id": plan.run_id,
            "result": "PASS" if all(item["status"] == "PASS" for item in results) else "FAIL",
            "profiles": results,
            "google_calendar_writes": False,
            "completed_at": datetime.now(UTC).isoformat(),
        }
        self.store.save_json_report(self.store.profile_preflight_report_path(plan.run_id), report)
        directory.mkdir(parents=True, exist_ok=True)
        return report

    def capture_profile_transition(
        self,
        plan: HybridCapturePlan,
        mode: HybridOutputMode,
        resolution: tuple[int, int],
    ) -> dict[str, object]:
        """Exercise the exact final A23 -> close A -> B24 capture path."""

        self.validate_final_capture_gate(plan, mode, resolution)
        self.validate_profile_preflight(plan)
        directory = self.store.profile_transition_directory(plan.run_id)
        report_path = self.store.profile_transition_report_path(plan.run_id)
        try:
            existing = self.store.load_json_report(report_path)
        except CalendarAnimError:
            existing = {}
        reusable = (
            existing.get("profile_navigation_version") == CURRENT_PROFILE_NAVIGATION_VERSION
            and existing.get("output_mode") == mode.value
            and existing.get("output_resolution") == list(resolution)
        )
        raw_results = existing.get("frames", []) if reusable else []
        results = (
            [item for item in raw_results if isinstance(item, dict)]
            if isinstance(raw_results, list)
            else []
        )
        report: dict[str, object] = {
            "schema_version": "1.0",
            "profile_navigation_version": CURRENT_PROFILE_NAVIGATION_VERSION,
            "run_id": plan.run_id,
            "output_mode": mode.value,
            "output_resolution": list(resolution),
            "result": "CAPTURING",
            "frames": results,
            "google_calendar_writes": False,
        }
        self.store.save_json_report(report_path, report)
        for frame_index, profile, zoom in ((22, "account-a", 33), (23, "account-b", 90)):
            frame = plan.frames[frame_index]
            output = directory / f"frame_{frame_index:03d}-{profile}.png"
            raw = directory / f".frame_{frame_index:03d}-{profile}-raw.png"
            logical = directory / f".frame_{frame_index:03d}-{profile}-logical.png"
            header = directory / f".frame_{frame_index:03d}-{profile}-header.png"
            item = next(
                (candidate for candidate in results if candidate.get("frame_index") == frame_index),
                None,
            )
            if item is not None and item.get("status") == "COMPLETED" and output.is_file():
                continue
            if item is None:
                item = {
                    "frame_index": frame_index,
                    "human_frame": frame.human_frame,
                    "profile": profile,
                    "week_start": frame.week_start.isoformat(),
                    "artifact": str(output),
                }
                results.append(item)
            item.update({"status": "CAPTURING", "error": None})
            self.store.save_json_report(report_path, report)
            try:
                with self.gateway_factory(profile, zoom) as gateway:
                    self._capture_composed_frame(
                        gateway,
                        frame,
                        raw,
                        logical,
                        header if mode.includes_header else None,
                        output,
                        mode,
                        resolution,
                        directory,
                    )
            except Exception as error:
                item.update({"status": "FAILED", "error": str(error)})
                report["result"] = "FAIL"
                self.store.save_json_report(report_path, report)
                raise
            for temporary in (raw, logical, header):
                if temporary.exists():
                    temporary.unlink()
            item["status"] = "COMPLETED"
            self.store.save_json_report(report_path, report)
        report["result"] = "PASS"
        report["completed_at"] = datetime.now(UTC).isoformat()
        self.store.save_json_report(report_path, report)
        return report

    def validate_profile_preflight(self, plan: HybridCapturePlan) -> dict[str, object]:
        report = self.store.load_json_report(self.store.profile_preflight_report_path(plan.run_id))
        profiles = report.get("profiles", [])
        profile_status = (
            {
                str(item.get("profile")): item.get("status")
                for item in profiles
                if isinstance(item, dict)
            }
            if isinstance(profiles, list)
            else {}
        )
        if (
            report.get("result") != "PASS"
            or report.get("profile_navigation_version") != CURRENT_PROFILE_NAVIGATION_VERSION
            or profile_status != {"account-a": "PASS", "account-b": "PASS"}
        ):
            raise CalendarAnimError("Final profile preflight is missing, stale, or not PASS")
        return report

    def validate_profile_capture_gates(
        self,
        plan: HybridCapturePlan,
        mode: HybridOutputMode,
        resolution: tuple[int, int],
    ) -> None:
        self.validate_profile_preflight(plan)
        transition = self.store.load_json_report(
            self.store.profile_transition_report_path(plan.run_id)
        )
        if (
            transition.get("result") != "PASS"
            or transition.get("profile_navigation_version") != CURRENT_PROFILE_NAVIGATION_VERSION
            or transition.get("output_mode") != mode.value
            or transition.get("output_resolution") != list(resolution)
        ):
            raise CalendarAnimError("Final profile transition test is missing, stale, or not PASS")
        for frame_index, profile in ((22, "account-a"), (23, "account-b")):
            artifact = (
                self.store.profile_transition_directory(plan.run_id)
                / f"frame_{frame_index:03d}-{profile}.png"
            )
            if not artifact.is_file():
                raise CalendarAnimError("Final profile transition artifact is missing")
            with Image.open(artifact) as image:
                if image.size != resolution:
                    raise CalendarAnimError("Final profile transition artifact has wrong geometry")

    def _capture_composed_frame(
        self,
        gateway: HybridBrowserGateway,
        frame: HybridFramePlan,
        raw: Path,
        logical: Path,
        header: Path | None,
        output: Path,
        mode: HybridOutputMode,
        resolution: tuple[int, int],
        debug_directory: Path,
    ) -> dict[str, object]:
        metrics = self._browser_capture(
            gateway,
            frame,
            raw,
            logical,
            header=header,
            debug_directory=debug_directory,
        )
        composition = compose_output_mode(
            logical,
            header,
            output,
            mode,
            resolution,
            native_header_height=_native_header_height(metrics) if mode.includes_header else None,
            native_time_gutter_width=(
                _native_time_gutter_width(metrics) if mode.includes_header else None
            ),
        )
        with Image.open(output) as image:
            if image.size != resolution:
                raise CalendarAnimError("Final normalized frame resolution is incorrect")
        metrics["composition"] = composition
        return metrics

    def validate_final_capture_gate(
        self,
        plan: HybridCapturePlan,
        mode: HybridOutputMode,
        resolution: tuple[int, int],
    ) -> FinalHybridSanityReport:
        """Require the current exact-configuration sanity before any browser capture."""

        sanity = self.store.load_final_sanity_report(plan.run_id, mode, resolution)
        if not final_sanity_allows_capture(sanity, mode, resolution):
            raise CalendarAnimError(
                "Current final sanity is stale or not PASS; rerun capture-hybrid-sanity"
            )
        return sanity

    def capture_seam(self, plan: HybridCapturePlan) -> HybridSeamReport:
        sanity = self.store.load_sanity_report(plan.run_id)
        if sanity.automated_result != "PASS":
            raise CalendarAnimError("Hybrid sanity is NO-GO; seam capture is blocked")
        metrics: dict[int, dict[str, object]] = {}
        paths: dict[int, Path] = {}
        for frame_index, profile, zoom in ((22, "account-a", 33), (23, "account-b", 90)):
            frame = plan.frames[frame_index]
            logical = (
                self.store.seam_directory(plan.run_id) / f"frame_{frame_index:03d}-logical.png"
            )
            normalized = (
                self.store.seam_directory(plan.run_id) / f"frame_{frame_index:03d}-normalized.png"
            )
            raw = self.store.seam_directory(plan.run_id) / f"frame_{frame_index:03d}-raw.png"
            with self.gateway_factory(profile, zoom) as gateway:
                metrics[frame_index] = self._browser_capture(gateway, frame, raw, logical)
            normalize_grid(logical, normalized)
            paths[frame_index] = normalized
        width_delta = _relative_delta(
            _number(metrics[22], "logical_cell_width"),
            _number(metrics[23], "logical_cell_width"),
        )
        height_delta = _relative_delta(
            _number(metrics[22], "logical_cell_height"),
            _number(metrics[23], "logical_cell_height"),
        )
        result = "PASS" if width_delta <= 0.05 and height_delta <= 0.05 else "NO-GO"
        report = HybridSeamReport(
            run_id=plan.run_id,
            cell_width_relative_delta=width_delta,
            cell_height_relative_delta=height_delta,
            geometry_result=result,
        )
        self.store.save_seam_report(report)
        compose_seam_geometry(
            paths[22],
            paths[23],
            self.store.seam_directory(plan.run_id) / "a-b-transition-geometry.png",
        )
        return report

    def _capture_sanity_frame(
        self, gateway: HybridBrowserGateway, plan: HybridCapturePlan, frame: HybridFramePlan
    ) -> SanityFrameResult:
        directory = self.store.sanity_frame_directory(plan.run_id, frame.human_frame)
        raw = directory / "raw.png"
        logical = directory / "logical-grid.png"
        normalized = directory / "normalized.png"
        expected = directory / "expected-local.png"
        frame_plan = _load_frame_plan(frame)
        render_expected_frame(frame_plan, expected)
        try:
            metrics = self._browser_capture(gateway, frame, raw, logical)
        except CaptureLoadFailure as error:
            last_sample = error.population_samples[-1] if error.population_samples else {}
            result = SanityFrameResult(
                frame_index=frame.frame_index,
                human_frame=frame.human_frame,
                profile=frame.calendar_profile,
                week_start=frame.week_start,
                expected_occurrences=frame.expected_occurrences,
                rendered_dom_events=int(_sample_number(last_sample, "unique_event_chips")),
                capture_success=False,
                capture_load_success=False,
                capture_error=str(error),
                capture_retry_cycles=max(0, len(error.errors) - 1),
                capture_timestamp=datetime.now(UTC),
                navigation_complete=False,
                stabilization_seconds=_sample_number(last_sample, "elapsed_seconds"),
                raw_dom_nodes=int(_sample_number(last_sample, "raw_dom_nodes")),
                unique_event_chips=int(_sample_number(last_sample, "unique_event_chips")),
                dom_population_samples=error.population_samples,
                normalized_width=0,
                normalized_height=0,
                logical_cell_width=0,
                logical_cell_height=0,
                expected_color_distribution=expected_distribution(frame_plan),
                rendered_color_distribution={},
                logical_cell_match_ratio=0,
                obvious_missing_content=True,
                obvious_color_mismatch=True,
                obvious_ordering_issue=True,
                unique_event_population_valid=False,
                grid_geometry_valid=False,
                colors_valid=False,
                ordering_valid=False,
                visual_match_valid=False,
                raw_artifact=str(error.diagnostic_paths[-1] if error.diagnostic_paths else raw),
                logical_artifact=str(logical),
                normalized_artifact=str(normalized),
                expected_artifact=str(expected),
            )
            write_atomic(
                directory / "metrics.json",
                json.dumps(
                    {
                        "capture_load_errors": error.errors,
                        "diagnostic_paths": [str(path) for path in error.diagnostic_paths],
                        "diagnostic_json_paths": [
                            str(path) for path in error.diagnostic_json_paths
                        ],
                        **result.model_dump(mode="json"),
                    },
                    indent=2,
                )
                + "\n",
            )
            return result
        normalize_grid(logical, normalized)
        match, classified = compare_logical_cells(expected, normalized)
        raw_rendered = metrics.get("rendered_color_counts")
        if not isinstance(raw_rendered, dict):
            raise CalendarAnimError("Calendar rendered color metrics are invalid")
        rendered = {str(key): int(value) for key, value in raw_rendered.items()}
        expected_colors = expected_distribution(frame_plan)
        dom_count = int(_number(metrics, "event_count"))
        population_ratio = dom_count / max(frame.expected_occurrences, 1)
        population_valid = 0.75 <= population_ratio <= 1.25
        colors_valid = len(rendered) >= max(1, len(expected_colors) - 1)
        ordering_valid = match >= 0.55
        raw_samples = metrics.get("dom_population_samples", [])
        if not isinstance(raw_samples, list) or not all(
            isinstance(sample, dict) for sample in raw_samples
        ):
            raise CalendarAnimError("Calendar DOM population samples are invalid")
        result = SanityFrameResult(
            frame_index=frame.frame_index,
            human_frame=frame.human_frame,
            profile=frame.calendar_profile,
            week_start=frame.week_start,
            expected_occurrences=frame.expected_occurrences,
            rendered_dom_events=dom_count,
            capture_success=True,
            capture_load_success=True,
            capture_retry_cycles=int(_number(metrics, "capture_retry_cycles")),
            capture_timestamp=datetime.now(UTC),
            navigation_complete=bool(metrics.get("navigation_complete", False)),
            stabilization_seconds=_number(metrics, "stabilization_seconds"),
            raw_dom_nodes=int(_number(metrics, "raw_dom_nodes")),
            unique_event_chips=int(_number(metrics, "unique_event_chips")),
            dom_population_samples=raw_samples,
            normalized_width=504,
            normalized_height=288,
            logical_cell_width=_number(metrics, "logical_cell_width"),
            logical_cell_height=_number(metrics, "logical_cell_height"),
            grid_left=_number(metrics, "grid_left"),
            grid_top=_number(metrics, "grid_top"),
            grid_right=_number(metrics, "grid_right"),
            grid_bottom=_number(metrics, "grid_bottom"),
            expected_color_distribution=expected_colors,
            rendered_color_distribution=rendered,
            logical_cell_match_ratio=match,
            obvious_missing_content=match < 0.55,
            obvious_color_mismatch=not colors_valid,
            obvious_ordering_issue=not ordering_valid,
            unique_event_population_valid=population_valid,
            colors_valid=colors_valid,
            ordering_valid=ordering_valid,
            visual_match_valid=ordering_valid,
            raw_artifact=str(raw),
            logical_artifact=str(logical),
            normalized_artifact=str(normalized),
            expected_artifact=str(expected),
        )
        payload = {
            **metrics,
            "classified_logical_colors": classified,
            **result.model_dump(mode="json"),
        }
        write_atomic(directory / "metrics.json", json.dumps(payload, indent=2) + "\n")
        return result

    @staticmethod
    def _browser_capture(
        gateway: HybridBrowserGateway,
        frame: HybridFramePlan,
        raw: Path,
        logical: Path,
        header: Path | None = None,
        debug_directory: Path | None = None,
        capture_context: dict[str, object] | None = None,
    ) -> dict[str, object]:
        errors: list[str] = []
        diagnostics: list[Path] = []
        diagnostic_json_paths: list[Path] = []
        population_samples: list[dict[str, object]] = []
        last_state: dict[str, object] = {}
        context = {
            "profile": frame.calendar_profile,
            "human_frame": frame.human_frame,
            "frame_index": frame.frame_index,
            "week_start": frame.week_start.isoformat(),
            "zoom_expected": frame.capture_zoom_percent,
            **(capture_context or {}),
        }
        for attempt in range(1, 4):
            try:
                if attempt == 1:
                    gateway.open_week(frame.week_start)
                    gateway.wait_until_ready(frame.week_start, 0)
                else:
                    gateway.reload_current_week(frame.week_start, 0)
                gateway.wait_for_animation_events(frame.expected_occurrences)
                gateway.capture_viewport(raw)
                metrics = gateway.capture_logical_event_grid(logical)
                if header is not None:
                    metrics.update(gateway.capture_header_event_grid(header))
                metrics["browser_debug"] = gateway.capture_debug_state()
                metrics["capture_retry_cycles"] = attempt - 1
                metrics.setdefault("stabilization_seconds", 0)
                metrics.setdefault("raw_dom_nodes", metrics.get("event_count", 0))
                metrics.setdefault("unique_event_chips", metrics.get("event_count", 0))
                metrics.setdefault("dom_population_samples", [])
                metrics.setdefault("navigation_complete", True)
                clip = metrics.get("logical_clip")
                if isinstance(clip, dict):
                    metrics.setdefault("grid_left", clip.get("x", 0))
                    metrics.setdefault("grid_top", clip.get("y", 0))
                    metrics.setdefault(
                        "grid_right",
                        _sample_number(clip, "x") + _sample_number(clip, "width"),
                    )
                    metrics.setdefault(
                        "grid_bottom",
                        _sample_number(clip, "y") + _sample_number(clip, "height"),
                    )
                else:
                    metrics.setdefault("grid_left", 0)
                    metrics.setdefault("grid_top", 0)
                    metrics.setdefault("grid_right", 0)
                    metrics.setdefault("grid_bottom", 0)
                return metrics
            except CalendarAnimError as error:
                errors.append(str(error))
                raw_samples = getattr(error, "samples", [])
                if isinstance(raw_samples, list):
                    for sample in raw_samples:
                        if isinstance(sample, dict):
                            population_samples.append({"attempt": attempt, **sample})
                directory = debug_directory or raw.parent
                basename = (
                    f"capture-failure-{frame.calendar_profile}-"
                    f"frame-{frame.human_frame:03d}-attempt-{attempt}"
                )
                diagnostic = directory / f"{basename}.png"
                diagnostic_json = directory / f"{basename}.json"
                try:
                    state = gateway.capture_debug_state()
                except Exception as debug_error:
                    state = {"diagnostic_error": str(debug_error)}
                last_state = state
                navigation = state.get("navigation", {}) if isinstance(state, dict) else {}
                if not isinstance(navigation, dict):
                    navigation = {}
                debug_payload = {
                    "attempt": attempt,
                    "reason": str(error),
                    **context,
                    "expected_week": frame.week_start.isoformat(),
                    "expected_occurrences_reference": frame.expected_occurrences,
                    "browser_profile_path": navigation.get("browser_profile_path"),
                    "current_url": navigation.get("current_url", state.get("url")),
                    "page_title": navigation.get("page_title", state.get("document_title")),
                    "logged_in_detection": navigation.get("logged_in_detection"),
                    "calendar_shell_detection": navigation.get("calendar_shell_detection"),
                    "week_view_detection": navigation.get("week_view_detection"),
                    "visible_week_date": navigation.get("visible_week_date"),
                    "navigation_state": navigation.get("state"),
                    "browser": state,
                }
                write_atomic(diagnostic_json, json.dumps(debug_payload, indent=2) + "\n")
                diagnostic_json_paths.append(diagnostic_json)
                try:
                    gateway.capture_viewport(diagnostic)
                    diagnostics.append(diagnostic)
                except Exception:
                    pass
                if bool(getattr(error, "non_retryable", False)):
                    break
        raise CaptureLoadFailure(
            errors,
            diagnostics,
            diagnostic_json_paths,
            population_samples,
            context,
            last_state,
        )

    def _validate_final_sequence(
        self,
        plan: HybridCapturePlan,
        mode: HybridOutputMode,
        resolution: tuple[int, int],
    ) -> None:
        paths = [
            self.store.final_frame_path(plan.run_id, index, mode, resolution)
            for index in range(108)
        ]
        if len(paths) != len(set(paths)) or any(not path.is_file() for path in paths):
            raise CalendarAnimError("Final hybrid frames contain a gap or duplicate")
        for path in paths:
            with Image.open(path) as image:
                if image.size != resolution:
                    raise CalendarAnimError(f"Final frame has wrong geometry: {path}")


def _load_frame_plan(frame: HybridFramePlan) -> SingleFrameCalendarPlan:
    try:
        return SingleFrameCalendarPlan.model_validate_json(
            Path(frame.source_frame_plan).read_text(encoding="utf-8")
        )
    except (OSError, ValueError) as error:
        raise CalendarAnimError(f"Invalid source frame plan: {frame.source_frame_plan}") from error


def _native_header_height(metrics: dict[str, object]) -> int:
    bounds = metrics.get("header_grid_bounds")
    if not isinstance(bounds, dict):
        raise CalendarAnimError("Native Calendar header bounds are unavailable")
    height = bounds.get("native_header_height")
    if not isinstance(height, int) or height <= 0:
        raise CalendarAnimError("Native Calendar header height is invalid")
    return height


def _native_time_gutter_width(metrics: dict[str, object]) -> int:
    bounds = metrics.get("header_grid_bounds")
    if not isinstance(bounds, dict):
        raise CalendarAnimError("Native Calendar time-gutter bounds are unavailable")
    width = bounds.get("native_time_gutter_width")
    if not isinstance(width, int) or width <= 0:
        raise CalendarAnimError("Native Calendar time-gutter width is invalid")
    return width


def _image_dimensions(path: Path) -> list[int]:
    try:
        with Image.open(path) as image:
            return [image.width, image.height]
    except OSError as error:
        raise CalendarAnimError(f"Could not inspect capture dimensions: {path}") from error


def _image_is_non_empty(path: Path) -> bool:
    try:
        with Image.open(path) as opened:
            rgb = opened.convert("RGB")
            colors = rgb.getcolors(maxcolors=2)
            rgb.close()
            return colors is None or len(colors) > 1
    except OSError as error:
        raise CalendarAnimError(f"Could not inspect final sanity image: {path}") from error


def _valid_clip(value: object) -> bool:
    if not isinstance(value, dict):
        return False
    width = value.get("width")
    height = value.get("height")
    return (
        isinstance(width, (int, float))
        and isinstance(height, (int, float))
        and width > 0
        and height > 0
    )


def final_sanity_allows_capture(
    report: FinalHybridSanityReport,
    mode: HybridOutputMode,
    resolution: tuple[int, int],
) -> bool:
    """Accept only a current, exact-configuration six-frame PASS."""

    return (
        report.schema_version == FINAL_SANITY_SCHEMA_VERSION
        and report.capture_implementation_version == CURRENT_CAPTURE_IMPLEMENTATION_VERSION
        and report.output_mode is mode
        and (report.output_width, report.output_height) == resolution
        and report.frames_checked == [24, 40, 60, 80, 100, 108]
        and len(report.results) == 6
        and report.automated_result == "PASS"
        and not report.dom_event_count_is_gate
        and not report.google_calendar_writes
        and all(item.passed for item in report.results)
    )


def final_sanity_gate_status(
    store: HybridCaptureStore,
    run_id: str,
    mode: HybridOutputMode,
    resolution: tuple[int, int],
) -> tuple[str, str | None]:
    """Describe whether the exact current sanity exists; legacy reports are stale."""

    current_path = store.final_sanity_report_path(run_id, mode, resolution)
    if current_path.is_file():
        try:
            report = store.load_final_sanity_report(run_id, mode, resolution)
        except CalendarAnimError:
            return "INVALID CURRENT REPORT - RERUN REQUIRED", None
        status = (
            "PASS"
            if final_sanity_allows_capture(report, mode, resolution)
            else "CURRENT REPORT NOT PASS - RERUN REQUIRED"
        )
        return status, report.capture_implementation_version
    legacy_path = store.sanity_directory(run_id) / "sanity-report.json"
    if legacy_path.is_file():
        try:
            legacy = json.loads(legacy_path.read_text(encoding="utf-8"))
            schema = (
                legacy.get("schema_version", "unknown") if isinstance(legacy, dict) else "unknown"
            )
        except (OSError, json.JSONDecodeError):
            schema = "invalid"
        return "STALE LEGACY REPORT - RERUN REQUIRED", f"legacy-schema-{schema}"
    return "NOT RUN", None


def _debug_mode_paths(directory: Path, resolution: tuple[int, int]) -> dict[HybridOutputMode, Path]:
    if resolution != LEGACY_RESOLUTION:
        return {
            HybridOutputMode.HEADER_PRESERVED_FILL: (
                directory / f"mode-c-{resolution[0]}x{resolution[1]}.png"
            )
        }
    return {
        HybridOutputMode.PIXEL_FAITHFUL: directory / "mode-a-pixel-faithful.png",
        HybridOutputMode.HEADER_PRESERVED_LETTERBOX: (
            directory / "mode-b-header-preserved-letterbox.png"
        ),
        HybridOutputMode.HEADER_PRESERVED_FILL: (directory / "mode-c-header-preserved-fill.png"),
    }


def _sanity_passes(result: SanityFrameResult) -> bool:
    return (
        result.capture_success
        and result.capture_load_success
        and (result.normalized_width, result.normalized_height) == (504, 288)
        and result.grid_geometry_valid
        and result.colors_valid
        and result.ordering_valid
        and result.visual_match_valid
    )


def _number(metrics: dict[str, object], key: str) -> float:
    value = metrics.get(key)
    if not isinstance(value, (int, float)):
        raise CalendarAnimError(f"Calendar metric {key} is invalid")
    return float(value)


def _preview_frame_result(
    frame: HybridFramePlan,
    output: Path,
    metrics: dict[str, object],
    resolution: tuple[int, int],
) -> SingleProfilePreviewFrameResult:
    composition = metrics.get("composition")
    browser = metrics.get("browser_debug")
    if not isinstance(composition, dict) or not isinstance(browser, dict):
        raise CalendarAnimError("Preview capture geometry metadata is incomplete")
    viewport = browser.get("viewport")
    source_dimensions = composition.get("source_dimensions")
    rect_names = (
        "header_source_rect",
        "time_gutter_source_rect",
        "grid_source_rect",
        "header_output_rect",
        "time_gutter_output_rect",
        "grid_output_rect",
    )
    rects = {name: composition.get(name) for name in rect_names}
    if not isinstance(viewport, dict):
        raise CalendarAnimError("Preview browser viewport metadata is invalid")
    if not (
        isinstance(source_dimensions, list)
        and len(source_dimensions) == 2
        and all(isinstance(value, int) for value in source_dimensions)
    ):
        raise CalendarAnimError("Preview native crop dimensions are invalid")
    if any(
        not isinstance(value, list)
        or len(value) != 4
        or not all(isinstance(item, int) for item in value)
        for value in rects.values()
    ):
        raise CalendarAnimError("Preview composition rectangles are invalid")
    navigation_complete = bool(metrics.get("navigation_complete"))
    return SingleProfilePreviewFrameResult(
        human_frame=frame.human_frame,
        frame_index=frame.frame_index,
        expected_week=frame.week_start,
        visible_week=frame.week_start if navigation_complete else None,
        week_validation="PASS" if navigation_complete else "FAIL",
        output=str(output),
        output_size=resolution,
        header_present=bool(composition.get("header_included")),
        left_time_gutter_present=bool(metrics.get("left_time_gutter_included")),
        timezone_label_present=bool(metrics.get("timezone_label_included")),
        create_button_excluded=bool(metrics.get("create_button_excluded")),
        pre_06_blank_gap_present=not bool(metrics.get("empty_pre_06_interval_removed")),
        vertical_interval=str(metrics.get("vertical_interval", "UNKNOWN")),
        capture="PASS",
        native_browser_viewport={str(key): value for key, value in viewport.items()},
        native_composed_crop_dimensions=(source_dimensions[0], source_dimensions[1]),
        header_source_rect=rects["header_source_rect"],  # type: ignore[arg-type]
        time_gutter_source_rect=rects["time_gutter_source_rect"],  # type: ignore[arg-type]
        grid_source_rect=rects["grid_source_rect"],  # type: ignore[arg-type]
        header_output_rect=rects["header_output_rect"],  # type: ignore[arg-type]
        time_gutter_output_rect=rects["time_gutter_output_rect"],  # type: ignore[arg-type]
        grid_output_rect=rects["grid_output_rect"],  # type: ignore[arg-type]
        current_url=str(browser.get("url")) if browser.get("url") else None,
    )


def _preview_geometry_signature(result: SingleProfilePreviewFrameResult) -> tuple[object, ...]:
    return (
        tuple(sorted(result.native_browser_viewport.items())),
        result.native_composed_crop_dimensions,
        tuple(result.header_source_rect),
        tuple(result.time_gutter_source_rect),
        tuple(result.grid_source_rect),
        tuple(result.header_output_rect),
        tuple(result.time_gutter_output_rect),
        tuple(result.grid_output_rect),
        result.output_size,
    )


def _single_profile_preview_text(report: SingleProfilePreviewReport) -> str:
    lines = [
        "FINAL SINGLE-PROFILE PREVIEW",
        "============================",
        "",
        f"Profile: {report.profile}",
        f"Zoom: {report.zoom_percent}%",
        f"Mode: {report.mode.value}",
        f"Resolution: {report.resolution[0]}x{report.resolution[1]}",
        f"Navigation version: {report.navigation_version}",
        "",
    ]
    for frame in report.frames:
        title = f"Frame {frame.human_frame}"
        lines.extend(
            [
                title,
                "-" * len(title),
                f"Human frame: {frame.human_frame}",
                f"Index: {frame.frame_index}",
                f"Expected week: {frame.expected_week}",
                f"Visible week: {frame.visible_week}",
                f"Week validation: {frame.week_validation}",
                f"Output: {frame.output}",
                f"Size: {frame.output_size[0]}x{frame.output_size[1]}",
                f"Header present: {'YES' if frame.header_present else 'NO'}",
                "Left time gutter present: " + ("YES" if frame.left_time_gutter_present else "NO"),
                "Timezone label present: " + ("YES" if frame.timezone_label_present else "NO"),
                "Create button excluded: " + ("YES" if frame.create_button_excluded else "NO"),
                "03:00-06:00 blank gap present: "
                + ("YES" if frame.pre_06_blank_gap_present else "NO"),
                f"Grid: {frame.vertical_interval}",
                f"Capture: {frame.capture}",
                f"Native viewport: {frame.native_browser_viewport}",
                f"Native composed crop: {frame.native_composed_crop_dimensions}",
                f"Header source rect: {frame.header_source_rect}",
                f"Left time gutter source rect: {frame.time_gutter_source_rect}",
                f"Grid source rect: {frame.grid_source_rect}",
                f"Header output rect: {frame.header_output_rect}",
                f"Left time gutter output rect: {frame.time_gutter_output_rect}",
                f"Grid output rect: {frame.grid_output_rect}",
                "",
            ]
        )
    lines.extend(
        [
            f"Frame23->24 delta: {report.frame_23_to_24_delta_days}",
            f"Geometry consistent: {'YES' if report.geometry_consistent else 'NO'}",
            f"Geometry warning: {report.geometry_warning or 'none'}",
            "Checkpoint touched: NO",
            "Full capture outputs touched: NO",
            "Account A opened: NO",
            "External Calendar/browser UI included: NO",
            "Google Calendar writes: NO",
            f"Preview: {report.preview}",
            "",
        ]
    )
    return "\n".join(lines)


def _relative_delta(left: float, right: float) -> float:
    return abs(left - right) / max(left, right, 1e-9)


def _sample_number(sample: dict[str, object], key: str) -> float:
    value = sample.get(key, 0)
    return float(value) if isinstance(value, (int, float)) else 0.0
