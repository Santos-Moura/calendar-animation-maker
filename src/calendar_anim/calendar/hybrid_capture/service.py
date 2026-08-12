import json
from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Protocol

from PIL import Image

from calendar_anim.calendar.frame_mapping.models import SingleFrameCalendarPlan
from calendar_anim.calendar.hybrid_capture.artifacts import (
    HybridCaptureStore,
    compare_logical_cells,
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

    def capture_final(
        self, plan: HybridCapturePlan, state: HybridCaptureState
    ) -> HybridCaptureState:
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
                    output = self.store.final_frame_path(plan.run_id, frame.frame_index)
                    if state_frame.status is HybridFrameStatus.COMPLETED and output.is_file():
                        continue
                    state_frame.status = HybridFrameStatus.CAPTURING
                    state_frame.started_at = datetime.now(UTC)
                    state_frame.error = None
                    self.store.save_state(state)
                    try:
                        raw = self.store.final_raw_path(plan.run_id, frame.frame_index)
                        logical = self.store.final_logical_path(plan.run_id, frame.frame_index)
                        self._browser_capture(gateway, frame, raw, logical)
                        normalize_grid(logical, output)
                        with Image.open(output) as image:
                            if image.size != (504, 288):
                                raise CalendarAnimError("Final normalized frame is not 504x288")
                    except Exception as error:
                        state_frame.status = HybridFrameStatus.FAILED
                        state_frame.error = str(error)
                        self.store.save_state(state)
                        raise
                    state_frame.status = HybridFrameStatus.COMPLETED
                    state_frame.completed_at = datetime.now(UTC)
                    self.store.save_state(state)
        self._validate_final_sequence(plan)
        compose_seam_geometry(
            self.store.final_frame_path(plan.run_id, 22),
            self.store.final_frame_path(plan.run_id, 23),
            self.store.run_directory(plan.run_id) / "seam" / "a-b-transition-geometry.png",
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
        metrics = self._browser_capture(gateway, frame, raw, logical)
        normalize_grid(logical, normalized)
        frame_plan = _load_frame_plan(frame)
        render_expected_frame(frame_plan, expected)
        match, classified = compare_logical_cells(expected, normalized)
        raw_rendered = metrics.get("rendered_color_counts")
        if not isinstance(raw_rendered, dict):
            raise CalendarAnimError("Calendar rendered color metrics are invalid")
        rendered = {str(key): int(value) for key, value in raw_rendered.items()}
        expected_colors = expected_distribution(frame_plan)
        dom_count = int(_number(metrics, "event_count"))
        result = SanityFrameResult(
            frame_index=frame.frame_index,
            human_frame=frame.human_frame,
            profile=frame.calendar_profile,
            week_start=frame.week_start,
            expected_occurrences=frame.expected_occurrences,
            rendered_dom_events=dom_count,
            capture_success=True,
            normalized_width=504,
            normalized_height=288,
            logical_cell_width=_number(metrics, "logical_cell_width"),
            logical_cell_height=_number(metrics, "logical_cell_height"),
            expected_color_distribution=expected_colors,
            rendered_color_distribution=rendered,
            logical_cell_match_ratio=match,
            obvious_missing_content=dom_count < frame.expected_occurrences * 0.5,
            obvious_color_mismatch=len(rendered) < max(1, len(expected_colors) - 1),
            obvious_ordering_issue=match < 0.55,
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
    ) -> dict[str, object]:
        gateway.open_week(frame.week_start)
        gateway.wait_until_ready(frame.week_start, frame.expected_occurrences)
        gateway.capture_viewport(raw)
        return gateway.capture_logical_event_grid(logical)

    def _validate_final_sequence(self, plan: HybridCapturePlan) -> None:
        paths = [self.store.final_frame_path(plan.run_id, index) for index in range(108)]
        if len(paths) != len(set(paths)) or any(not path.is_file() for path in paths):
            raise CalendarAnimError("Final hybrid frames contain a gap or duplicate")
        for path in paths:
            with Image.open(path) as image:
                if image.size != (504, 288):
                    raise CalendarAnimError(f"Final frame has wrong geometry: {path}")


def _load_frame_plan(frame: HybridFramePlan) -> SingleFrameCalendarPlan:
    try:
        return SingleFrameCalendarPlan.model_validate_json(
            Path(frame.source_frame_plan).read_text(encoding="utf-8")
        )
    except (OSError, ValueError) as error:
        raise CalendarAnimError(f"Invalid source frame plan: {frame.source_frame_plan}") from error


def _sanity_passes(result: SanityFrameResult) -> bool:
    return (
        result.capture_success
        and (result.normalized_width, result.normalized_height) == (504, 288)
        and not result.obvious_missing_content
        and not result.obvious_color_mismatch
        and not result.obvious_ordering_issue
    )


def _number(metrics: dict[str, object], key: str) -> float:
    value = metrics.get(key)
    if not isinstance(value, (int, float)):
        raise CalendarAnimError(f"Calendar metric {key} is invalid")
    return float(value)


def _relative_delta(left: float, right: float) -> float:
    return abs(left - right) / max(left, right, 1e-9)
