from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from calendar_anim.calendar.calibration.models import CalibrationProfile
from calendar_anim.calendar.frame_mapping.colors import (
    DEFAULT_CALENDAR_BACKGROUND,
    map_calendar_color,
)
from calendar_anim.calendar.frame_mapping.models import (
    CalendarMappedCell,
    FitMode,
    FrameMappingStatistics,
    LogicalCell,
    SingleFrameCalendarPlan,
)
from calendar_anim.calendar.models import CalendarEventDraft
from calendar_anim.exceptions import CalendarAnimError
from calendar_anim.models.animation import AnimationManifest
from calendar_anim.models.frame import AnimationFrame

SUPPORTED_HORIZONTAL_STRATEGIES = {"independent-cells", "unit-cells-only"}
WEEKDAY_INDEX = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}


def select_frame(manifest: AnimationManifest, frame_index: int) -> AnimationFrame:
    if frame_index < 0 or frame_index >= len(manifest.frames):
        last = len(manifest.frames) - 1
        raise CalendarAnimError(
            f"Frame index {frame_index} is out of range. Manifest contains "
            f"{len(manifest.frames)} frames (0-{last})."
        )
    frame = manifest.frames[frame_index]
    if frame.index != frame_index:
        raise CalendarAnimError(
            f"Manifest frame at position {frame_index} has unexpected index {frame.index}"
        )
    return frame


def expand_frame_blocks(
    frame: AnimationFrame, source_width: int, source_height: int
) -> list[LogicalCell]:
    cells: list[LogicalCell] = []
    occupied: set[tuple[int, int]] = set()
    for block_index, block in enumerate(frame.blocks):
        if block.x + block.width > source_width or block.y + block.height > source_height:
            raise CalendarAnimError(
                f"Frame {frame.index} block {block_index} exceeds source grid "
                f"{source_width}x{source_height}"
            )
        for y in range(block.y, block.y + block.height):
            for x in range(block.x, block.x + block.width):
                if (x, y) in occupied:
                    raise CalendarAnimError(
                        f"Frame {frame.index} has overlapping blocks at cell ({x}, {y})"
                    )
                occupied.add((x, y))
                cells.append(
                    LogicalCell(
                        x=x,
                        y=y,
                        source_x=x,
                        source_y=y,
                        color_hex=block.color_hex.upper(),
                        source_block_index=block_index,
                    )
                )
    return cells


def fit_cells_contain(
    cells: list[LogicalCell],
    source_width: int,
    source_height: int,
    target_width: int,
    target_height: int,
) -> list[LogicalCell]:
    if min(source_width, source_height, target_width, target_height) <= 0:
        raise CalendarAnimError("source and target grid dimensions must be positive")
    scale = min(target_width / source_width, target_height / source_height)
    fitted_width = max(1, min(target_width, int(source_width * scale + 0.5)))
    fitted_height = max(1, min(target_height, int(source_height * scale + 0.5)))
    offset_x = (target_width - fitted_width) // 2
    offset_y = (target_height - fitted_height) // 2
    source_cells = {(cell.x, cell.y): cell for cell in cells}
    fitted: list[LogicalCell] = []
    for local_y in range(fitted_height):
        source_y = min(source_height - 1, local_y * source_height // fitted_height)
        for local_x in range(fitted_width):
            source_x = min(source_width - 1, local_x * source_width // fitted_width)
            source = source_cells.get((source_x, source_y))
            if source is None:
                continue
            fitted.append(
                LogicalCell(
                    x=offset_x + local_x,
                    y=offset_y + local_y,
                    source_x=source.source_x,
                    source_y=source.source_y,
                    color_hex=source.color_hex,
                    source_block_index=source.source_block_index,
                )
            )
    return fitted


def resolve_week_start(anchor: date, week_starts_on: str) -> date:
    normalized = week_starts_on.lower()
    if normalized not in WEEKDAY_INDEX:
        raise CalendarAnimError(f"Unsupported week start: {week_starts_on!r}")
    delta = (anchor.weekday() - WEEKDAY_INDEX[normalized]) % 7
    return anchor - timedelta(days=delta)


def map_cells_to_calendar(
    cells: list[LogicalCell],
    profile: CalibrationProfile,
    week_start_date: date,
    timezone: str,
    animation_id: str,
    run_id: str,
    frame_index: int,
    background_hex: str,
) -> tuple[list[CalendarMappedCell], list[CalendarEventDraft]]:
    columns_per_day = profile.horizontal_mapping.usable_overlap_columns_per_day
    row_minutes = profile.vertical_mapping.minimum_distinguishable_height_minutes
    target_width = profile.candidate_grid.width
    target_height = profile.candidate_grid.height
    if columns_per_day is None or row_minutes is None:
        raise CalendarAnimError("Calibration profile is missing row or overlap measurements")
    if target_width is None or target_height is None:
        raise CalendarAnimError("Calibration profile has no candidate grid")
    try:
        zone = ZoneInfo(timezone)
    except ZoneInfoNotFoundError as error:
        raise CalendarAnimError(f"Unknown timezone: {timezone}") from error

    allowed_colors = profile.color_mapping.preferred_color_ids
    mapped: list[CalendarMappedCell] = []
    events: list[CalendarEventDraft] = []
    for cell in sorted(cells, key=lambda item: (item.y, item.x)):
        if cell.x >= target_width or cell.y >= target_height:
            raise CalendarAnimError(
                f"Fitted cell ({cell.x}, {cell.y}) exceeds target grid "
                f"{target_width}x{target_height}"
            )
        day_offset = cell.x // columns_per_day
        subcolumn = cell.x % columns_per_day
        if day_offset >= profile.horizontal_mapping.days_used:
            raise CalendarAnimError(f"Logical x={cell.x} exceeds calibrated day capacity")
        event_day = week_start_date + timedelta(days=day_offset)
        start = datetime.combine(
            event_day,
            time(profile.calendar_ui.visible_start_hour),
            zone,
        ) + timedelta(minutes=cell.y * row_minutes)
        end = start + timedelta(minutes=row_minutes)
        if end > datetime.combine(
            event_day,
            time(profile.calendar_ui.visible_end_hour % 24),
            zone,
        ) + (timedelta(days=1) if profile.calendar_ui.visible_end_hour == 24 else timedelta()):
            raise CalendarAnimError(f"Logical y={cell.y} exceeds calibrated visible hours")
        calendar_color = map_calendar_color(cell.color_hex, allowed_colors, background_hex)
        mapped_cell = CalendarMappedCell(
            logical_x=cell.x,
            logical_y=cell.y,
            source_x=cell.source_x,
            source_y=cell.source_y,
            day_offset=day_offset,
            subcolumn=subcolumn,
            start=start,
            end=end,
            color_id=calendar_color.id,
            color_hex=calendar_color.hex,
            source_block_index=cell.source_block_index,
        )
        mapped.append(mapped_cell)
        events.append(
            CalendarEventDraft(
                frame_index=frame_index,
                block_index=cell.source_block_index,
                start=start,
                end=end,
                color_id=calendar_color.id,
                color_hex=calendar_color.hex,
                summary=f"calendar-anim:{animation_id}:frame-{frame_index}",
                private_metadata={
                    "generated_by": "calendar-anim",
                    "animation_id": animation_id,
                    "run_id": run_id,
                    "frame_index": str(frame_index),
                    "logical_x": str(cell.x),
                    "logical_y": str(cell.y),
                    "subcolumn": str(subcolumn),
                    "source_block_index": str(cell.source_block_index),
                },
            )
        )
    return mapped, events


def build_single_frame_plan(
    manifest: AnimationManifest,
    profile: CalibrationProfile,
    frame_index: int,
    anchor_date: date,
    run_id: str,
    max_execute_events: int,
    fit: FitMode = "contain",
) -> SingleFrameCalendarPlan:
    if fit != "contain":
        raise CalendarAnimError(f"Unsupported frame fit: {fit}")
    if max_execute_events <= 0:
        raise CalendarAnimError("max execute events must be positive")
    frame = select_frame(manifest, frame_index)
    target_width = profile.candidate_grid.width
    target_height = profile.candidate_grid.height
    week_starts_on = profile.position_mapping.week_starts_on
    if target_width is None or target_height is None:
        raise CalendarAnimError("Calibration profile has no candidate grid")
    if week_starts_on is None:
        raise CalendarAnimError(
            "Calibration profile has no week_starts_on observation; record position-grid first"
        )
    week_start_date = resolve_week_start(anchor_date, week_starts_on)
    expanded = expand_frame_blocks(frame, manifest.render.grid_width, manifest.render.grid_height)
    fitted = fit_cells_contain(
        expanded,
        manifest.render.grid_width,
        manifest.render.grid_height,
        target_width,
        target_height,
    )
    recorded_strategy = profile.horizontal_bar_mapping.recommended_horizontal_strategy
    horizontal_strategy = recorded_strategy or "unit-cells-only"
    if horizontal_strategy not in SUPPORTED_HORIZONTAL_STRATEGIES:
        raise CalendarAnimError(
            f"Horizontal strategy is not supported by the single-frame mapper: "
            f"{horizontal_strategy}"
        )
    background_hex = manifest.render.background or DEFAULT_CALENDAR_BACKGROUND
    mapped, events = map_cells_to_calendar(
        fitted,
        profile,
        week_start_date,
        profile.calendar_ui.timezone,
        manifest.animation_id,
        run_id,
        frame_index,
        background_hex,
    )
    warnings = [
        "Calendar has no subcolumn field; placement is inferred from simultaneous events "
        "and must be verified visually."
    ]
    if not profile.mapper_ready:
        warnings.append(
            "Calibration profile is NOT READY; dry-run is allowed but real upload is blocked."
        )
    if recorded_strategy is None:
        warnings.append("Horizontal strategy is uncalibrated; this dry-run uses unit-cells-only.")
    if len(events) > max_execute_events:
        warnings.append(
            f"Event count {len(events)} exceeds the configured execute limit {max_execute_events}."
        )
    event_count = len(events)
    mapped_count = len(mapped)
    unique_colors = len({cell.color_id for cell in mapped})
    return SingleFrameCalendarPlan(
        animation_id=manifest.animation_id,
        run_id=run_id,
        frame_index=frame_index,
        timezone=profile.calendar_ui.timezone,
        week_start_date=week_start_date,
        source_grid_width=manifest.render.grid_width,
        source_grid_height=manifest.render.grid_height,
        target_grid_width=target_width,
        target_grid_height=target_height,
        fit=fit,
        profile_ready=profile.mapper_ready,
        horizontal_strategy=horizontal_strategy,
        max_execute_events=max_execute_events,
        warnings=warnings,
        statistics=FrameMappingStatistics(
            source_blocks=len(frame.blocks),
            expanded_logical_cells=len(expanded),
            non_background_cells=len(expanded),
            mapped_cells=mapped_count,
            calendar_events=event_count,
            unique_calendar_colors=unique_colors,
            cells_per_event=(mapped_count / event_count if event_count else 0),
            compression_ratio=(event_count / mapped_count if mapped_count else 0),
        ),
        mapped_cells=mapped,
        events=events,
    )
