from collections.abc import Sequence
from datetime import date

from calendar_anim.calendar.calibration.models import CalibrationProfile
from calendar_anim.calendar.frame_mapping.mapper import build_single_frame_plan
from calendar_anim.calendar.frame_mapping.models import (
    CalendarMappedCell,
    EventCompressionMode,
    FrameMappingMode,
)
from calendar_anim.calendar.frame_mapping.service import ABSOLUTE_SINGLE_FRAME_MAX_EVENTS
from calendar_anim.calendar.vertical_compression.models import (
    AnimationVerticalCompressionEstimate,
    FrameVerticalCompressionEstimate,
    VerticalRun,
)
from calendar_anim.exceptions import CalendarAnimError
from calendar_anim.models.animation import AnimationManifest

ESTIMATE_ANCHOR_DATE = date(2026, 1, 4)


def estimate_vertical_runs(
    cells: Sequence[CalendarMappedCell],
    width: int,
    height: int,
    frame_index: int,
) -> FrameVerticalCompressionEstimate:
    expected_cells = width * height
    if len(cells) != expected_cells:
        raise CalendarAnimError(
            f"Vertical compression requires a complete {width}x{height} canvas; "
            f"received {len(cells)} cells instead of {expected_cells}"
        )
    by_coordinate = {(cell.logical_x, cell.logical_y): cell for cell in cells}
    if len(by_coordinate) != expected_cells:
        raise CalendarAnimError("Vertical compression canvas contains duplicate coordinates")

    runs: list[VerticalRun] = []
    for logical_x in range(width):
        start_y = 0
        current = _cell_at(by_coordinate, logical_x, 0)
        for logical_y in range(1, height + 1):
            next_cell = (
                _cell_at(by_coordinate, logical_x, logical_y) if logical_y < height else None
            )
            if next_cell is not None and _compatible(current, next_cell):
                continue
            runs.append(
                VerticalRun(
                    logical_x=logical_x,
                    start_y=start_y,
                    length=logical_y - start_y,
                    color_id=current.color_id,
                    cell_role=current.cell_role,
                )
            )
            if next_cell is not None:
                start_y = logical_y
                current = next_cell

    compressed = len(runs)
    saved = expected_cells - compressed
    return FrameVerticalCompressionEstimate(
        frame_index=frame_index,
        baseline_events=expected_cells,
        compressed_runs=compressed,
        saved_events=saved,
        reduction_percent=_percentage(saved, expected_cells),
        foreground_runs=sum(run.cell_role.value == "foreground" for run in runs),
        background_runs=sum(run.cell_role.value == "background" for run in runs),
        longest_vertical_run=max((run.length for run in runs), default=0),
        average_run_length=round(expected_cells / compressed, 3) if compressed else 0,
        runs=runs,
    )


def estimate_manifest_vertical_compression(
    manifest: AnimationManifest,
    profile: CalibrationProfile,
    calendar_background_color_id: str | None = None,
) -> AnimationVerticalCompressionEstimate:
    frame_estimates: list[FrameVerticalCompressionEstimate] = []
    for frame in manifest.frames:
        plan = build_single_frame_plan(
            manifest,
            profile,
            frame_index=frame.index,
            anchor_date=ESTIMATE_ANCHOR_DATE,
            run_id=f"compression-estimate-{frame.index:04d}",
            max_execute_events=ABSOLUTE_SINGLE_FRAME_MAX_EVENTS,
            mapping_mode=FrameMappingMode.FULL_GRID,
            event_compression=EventCompressionMode.NONE,
            calendar_background_color_id=calendar_background_color_id,
        )
        frame_estimates.append(
            estimate_vertical_runs(
                plan.mapped_cells,
                plan.target_grid_width,
                plan.target_grid_height,
                frame.index,
            )
        )

    if not frame_estimates:
        raise CalendarAnimError("Manifest contains no frames")
    baseline = sum(frame.baseline_events for frame in frame_estimates)
    compressed = sum(frame.compressed_runs for frame in frame_estimates)
    saved = baseline - compressed
    return AnimationVerticalCompressionEstimate(
        animation_id=manifest.animation_id,
        grid_width=profile.candidate_grid.width or 0,
        grid_height=profile.candidate_grid.height or 0,
        frames=frame_estimates,
        total_baseline_events=baseline,
        total_compressed_runs=compressed,
        total_saved_events=saved,
        total_reduction_percent=_percentage(saved, baseline),
        total_foreground_runs=sum(frame.foreground_runs for frame in frame_estimates),
        total_background_runs=sum(frame.background_runs for frame in frame_estimates),
        longest_vertical_run=max(frame.longest_vertical_run for frame in frame_estimates),
        average_run_length=round(baseline / compressed, 3) if compressed else 0,
    )


def _cell_at(
    cells: dict[tuple[int, int], CalendarMappedCell], logical_x: int, logical_y: int
) -> CalendarMappedCell:
    try:
        return cells[(logical_x, logical_y)]
    except KeyError as error:
        raise CalendarAnimError(
            f"Vertical compression canvas is missing cell ({logical_x}, {logical_y})"
        ) from error


def _compatible(left: CalendarMappedCell, right: CalendarMappedCell) -> bool:
    return left.color_id == right.color_id and left.cell_role is right.cell_role


def _percentage(part: int, whole: int) -> float:
    return round((part / whole) * 100, 1) if whole else 0
