import json
import statistics
from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Protocol

from PIL import Image

from calendar_anim.calendar.frame_mapping.models import SingleFrameCalendarPlan
from calendar_anim.calendar.hybrid_capture.artifacts import (
    LEGACY_RESOLUTION,
    HybridCaptureStore,
    compare_logical_cells,
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
    HybridCapturePlan,
    HybridCaptureState,
    HybridFramePlan,
    HybridFrameStatus,
    HybridOutputMode,
    HybridSanityReport,
    HybridSeamReport,
    SanityFrameResult,
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


class CaptureLoadFailure(CalendarAnimError):
    def __init__(
        self,
        errors: list[str],
        diagnostic_paths: list[Path],
        diagnostic_json_paths: list[Path],
        population_samples: list[dict[str, object]],
    ) -> None:
        super().__init__(f"CAPTURE LOAD FAILURE after {len(errors)} attempts: {errors[-1]}")
        self.errors = errors
        self.diagnostic_paths = diagnostic_paths
        self.diagnostic_json_paths = diagnostic_json_paths
        self.population_samples = population_samples


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
        sanity = self.store.load_sanity_report(plan.run_id)
        if sanity.automated_result != "PASS":
            raise CalendarAnimError("Hybrid sanity is NO-GO; full capture is blocked")
        seam = self.store.load_seam_report(plan.run_id)
        if seam.geometry_result != "PASS":
            raise CalendarAnimError("A/B seam geometry is NO-GO; full capture is blocked")
        for profile, zoom in (("account-a", 33), ("account-b", 90)):
            frames = [frame for frame in plan.frames if frame.calendar_profile == profile]
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
                        metrics = self._browser_capture(gateway, frame, raw, logical, header=header)
                        compose_output_mode(
                            logical,
                            header,
                            output,
                            mode,
                            resolution,
                            native_header_height=(
                                _native_header_height(metrics) if mode.includes_header else None
                            ),
                        )
                        with Image.open(output) as image:
                            if image.size != resolution:
                                raise CalendarAnimError(
                                    "Final normalized frame resolution is incorrect"
                                )
                    except Exception as error:
                        state_frame.status = HybridFrameStatus.FAILED
                        state_frame.error = str(error)
                        self.store.save_state(state)
                        raise
                    state_frame.status = HybridFrameStatus.COMPLETED
                    state_frame.completed_at = datetime.now(UTC)
                    self.store.save_state(state)
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
    ) -> dict[str, object]:
        errors: list[str] = []
        diagnostics: list[Path] = []
        diagnostic_json_paths: list[Path] = []
        population_samples: list[dict[str, object]] = []
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
                diagnostic = directory / f"debug-attempt-{attempt}.png"
                diagnostic_json = directory / f"debug-attempt-{attempt}.json"
                try:
                    state = gateway.capture_debug_state()
                except Exception as debug_error:
                    state = {"diagnostic_error": str(debug_error)}
                debug_payload = {
                    "attempt": attempt,
                    "reason": str(error),
                    "expected_week": frame.week_start.isoformat(),
                    "expected_occurrences_reference": frame.expected_occurrences,
                    "browser": state,
                }
                write_atomic(diagnostic_json, json.dumps(debug_payload, indent=2) + "\n")
                diagnostic_json_paths.append(diagnostic_json)
                try:
                    gateway.capture_viewport(diagnostic)
                    diagnostics.append(diagnostic)
                except Exception:
                    pass
        raise CaptureLoadFailure(errors, diagnostics, diagnostic_json_paths, population_samples)

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


def _image_dimensions(path: Path) -> list[int]:
    try:
        with Image.open(path) as image:
            return [image.width, image.height]
    except OSError as error:
        raise CalendarAnimError(f"Could not inspect capture dimensions: {path}") from error


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


def _relative_delta(left: float, right: float) -> float:
    return abs(left - right) / max(left, right, 1e-9)


def _sample_number(sample: dict[str, object], key: str) -> float:
    value = sample.get(key, 0)
    return float(value) if isinstance(value, (int, float)) else 0.0
