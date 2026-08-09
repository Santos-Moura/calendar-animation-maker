from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from calendar_anim.calendar.calibration.models import CalibrationProfile
from calendar_anim.calendar.frame_mapping.colors import (
    DEFAULT_CALENDAR_BACKGROUND,
    calendar_palette_color,
    map_calendar_color,
)
from calendar_anim.calendar.frame_mapping.models import (
    DEFAULT_EVENT_COMPRESSION,
    CalendarMappedCell,
    CellRole,
    EventCompressionMode,
    FitMode,
    FrameMappingMode,
    FrameMappingStatistics,
    LogicalCell,
    SingleFrameCalendarPlan,
)
from calendar_anim.calendar.horizontal_band_compression.bands import (
    build_synchronized_horizontal_bands,
)
from calendar_anim.calendar.models import CalendarEventDraft
from calendar_anim.calendar.subcolumn_ordering import (
    SubcolumnOrderStrategy,
    parse_subcolumn_order_strategy,
    summary_for_subcolumn,
    summary_order_keys,
)
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


def logical_cell_calendar_order_key(
    cell: LogicalCell, columns_per_day: int
) -> tuple[int, int, int]:
    """Return the explicit gateway submission order: day, row, then subcolumn."""

    return (cell.x // columns_per_day, cell.y, cell.x % columns_per_day)


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
    frame: AnimationFrame,
    source_width: int,
    source_height: int,
    source_background: str | None = None,
) -> list[LogicalCell]:
    """Reconstruct foreground, excluding legacy blocks matching the source background."""

    cells: list[LogicalCell] = []
    occupied: set[tuple[int, int]] = set()
    normalized_background = source_background.upper() if source_background is not None else None
    for block_index, block in enumerate(frame.blocks):
        if block.x + block.width > source_width or block.y + block.height > source_height:
            raise CalendarAnimError(
                f"Frame {frame.index} block {block_index} exceeds source grid "
                f"{source_width}x{source_height}"
            )
        if normalized_background is not None and block.color_hex.upper() == normalized_background:
            continue
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
                        cell_role=CellRole.FOREGROUND,
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
    """Fit foreground into the target without stretching or generating background."""

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
                    cell_role=CellRole.FOREGROUND,
                )
            )
    return fitted


def build_sparse_cells(foreground_cells: list[LogicalCell]) -> list[LogicalCell]:
    """Keep only fitted foreground cells for the event-efficient legacy mode."""

    return sorted(foreground_cells, key=lambda cell: (cell.y, cell.x))


def build_full_grid_cells(
    foreground_cells: list[LogicalCell],
    target_width: int,
    target_height: int,
    background_color_hex: str,
) -> list[LogicalCell]:
    """Complete the target canvas with structural Calendar background cells."""

    if target_width <= 0 or target_height <= 0:
        raise CalendarAnimError("target grid dimensions must be positive")
    foreground_by_position: dict[tuple[int, int], LogicalCell] = {}
    for cell in foreground_cells:
        if cell.x >= target_width or cell.y >= target_height:
            raise CalendarAnimError(
                f"Fitted cell ({cell.x}, {cell.y}) exceeds target grid "
                f"{target_width}x{target_height}"
            )
        position = (cell.x, cell.y)
        if position in foreground_by_position:
            raise CalendarAnimError(f"Duplicate fitted cell at ({cell.x}, {cell.y})")
        foreground_by_position[position] = cell

    canvas: list[LogicalCell] = []
    for y in range(target_height):
        for x in range(target_width):
            foreground = foreground_by_position.get((x, y))
            if foreground is not None:
                canvas.append(foreground)
            else:
                canvas.append(
                    LogicalCell(
                        x=x,
                        y=y,
                        color_hex=background_color_hex,
                        cell_role=CellRole.BACKGROUND,
                    )
                )
    return canvas


def generate_mapping_cells(
    mode: FrameMappingMode,
    foreground_cells: list[LogicalCell],
    target_width: int,
    target_height: int,
    background_color_hex: str,
) -> list[LogicalCell]:
    """Select one of the two explicit cell-generation strategies."""

    if mode is FrameMappingMode.SPARSE:
        return build_sparse_cells(foreground_cells)
    return build_full_grid_cells(
        foreground_cells,
        target_width,
        target_height,
        background_color_hex,
    )


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
    background_color_id: str | None = None,
    subcolumn_order_strategy: str | SubcolumnOrderStrategy = SubcolumnOrderStrategy.NONE,
) -> tuple[list[CalendarMappedCell], list[CalendarEventDraft]]:
    columns_per_day = profile.horizontal_mapping.usable_overlap_columns_per_day
    row_minutes = profile.vertical_mapping.minimum_distinguishable_height_minutes
    target_width = profile.candidate_grid.width
    target_height = profile.candidate_grid.height
    if columns_per_day is None or row_minutes is None:
        raise CalendarAnimError("Calibration profile is missing row or overlap measurements")
    if target_width is None or target_height is None:
        raise CalendarAnimError("Calibration profile has no candidate grid")
    ordering_strategy = parse_subcolumn_order_strategy(subcolumn_order_strategy)
    try:
        zone = ZoneInfo(timezone)
    except ZoneInfoNotFoundError as error:
        raise CalendarAnimError(f"Unknown timezone: {timezone}") from error

    allowed_colors = profile.color_mapping.preferred_color_ids
    structural_background = (
        calendar_palette_color(background_color_id) if background_color_id is not None else None
    )
    mapped: list[CalendarMappedCell] = []
    events: list[CalendarEventDraft] = []
    ordered_cells = sorted(
        cells, key=lambda cell: logical_cell_calendar_order_key(cell, columns_per_day)
    )
    for cell in ordered_cells:
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
        visible_end = datetime.combine(
            event_day,
            time(profile.calendar_ui.visible_end_hour % 24),
            zone,
        ) + (timedelta(days=1) if profile.calendar_ui.visible_end_hour == 24 else timedelta())
        if end > visible_end:
            raise CalendarAnimError(f"Logical y={cell.y} exceeds calibrated visible hours")
        if cell.cell_role is CellRole.BACKGROUND:
            if structural_background is None:
                raise CalendarAnimError("Structural background cell has no background color ID")
            calendar_color = structural_background
        else:
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
            cell_role=cell.cell_role,
        )
        metadata = {
            "generated_by": "calendar-anim",
            "animation_id": animation_id,
            "run_id": run_id,
            "frame_index": str(frame_index),
            "logical_x": str(cell.x),
            "logical_y": str(cell.y),
            "day_offset": str(day_offset),
            "subcolumn": str(subcolumn),
            "subcolumn_index": str(subcolumn),
            "subcolumn_order_strategy": ordering_strategy.value,
            "cell_role": cell.cell_role.value,
        }
        summary = summary_for_subcolumn(subcolumn, columns_per_day, ordering_strategy)
        if ordering_strategy is SubcolumnOrderStrategy.SUMMARY_PREFIX:
            metadata["subcolumn_order_key"] = summary
        if cell.source_block_index is not None:
            metadata["source_block_index"] = str(cell.source_block_index)
        mapped.append(mapped_cell)
        events.append(
            CalendarEventDraft(
                frame_index=frame_index,
                block_index=cell.source_block_index,
                start=start,
                end=end,
                color_id=calendar_color.id,
                color_hex=calendar_color.hex,
                summary=summary,
                private_metadata=metadata,
            )
        )
    return mapped, events


def synchronized_horizontal_bands_ready(profile: CalibrationProfile) -> bool:
    observations = profile.synchronized_horizontal_bands
    if observations is None:
        return False
    return all(
        value is True
        for value in (
            observations.equal_widths_preserved,
            observations.slot_order_preserved,
            observations.color_vectors_preserved,
            observations.adjacent_boundaries_stable,
            observations.stable_after_refresh,
            observations.stable_after_navigation,
            observations.visually_acceptable,
            observations.safe_for_mapper,
        )
    )


def compress_events_into_synchronized_horizontal_bands(
    mapped_cells: list[CalendarMappedCell],
    events: list[CalendarEventDraft],
    target_width: int,
    target_height: int,
    columns_per_day: int,
    days_used: int,
) -> tuple[list[CalendarEventDraft], int]:
    """Merge equal consecutive row vectors while retaining six events per band."""

    if len(mapped_cells) != len(events):
        raise CalendarAnimError("Baseline mapped cells and Calendar events do not match")
    bands, _ = build_synchronized_horizontal_bands(
        mapped_cells,
        target_width,
        target_height,
        columns_per_day,
        days_used,
    )
    cells_by_coordinate = {(cell.logical_x, cell.logical_y): cell for cell in mapped_cells}
    events_by_coordinate = {
        (
            int(event.private_metadata["logical_x"]),
            int(event.private_metadata["logical_y"]),
        ): event
        for event in events
    }
    compressed: list[CalendarEventDraft] = []
    for band_index, band in enumerate(bands):
        for slot in band.slots:
            logical_x = band.day_offset * columns_per_day + slot.subcolumn
            coordinates = [
                (logical_x, logical_y)
                for logical_y in range(band.start_y, band.start_y + band.length)
            ]
            first = events_by_coordinate[coordinates[0]]
            last = events_by_coordinate[coordinates[-1]]
            source_indexes = {
                cell.source_block_index
                for coordinate in coordinates
                if (cell := cells_by_coordinate[coordinate]).source_block_index is not None
            }
            source_block_index = next(iter(source_indexes)) if len(source_indexes) == 1 else None
            metadata = dict(first.private_metadata)
            metadata.update(
                {
                    "event_compression": (EventCompressionMode.SYNCHRONIZED_HORIZONTAL_BANDS.value),
                    "band_index": str(band_index),
                    "band_start_y": str(band.start_y),
                    "band_end_y_exclusive": str(band.start_y + band.length),
                    "band_length_rows": str(band.length),
                }
            )
            if source_block_index is None:
                metadata.pop("source_block_index", None)
            else:
                metadata["source_block_index"] = str(source_block_index)
            compressed.append(
                CalendarEventDraft(
                    frame_index=first.frame_index,
                    block_index=source_block_index,
                    start=first.start,
                    end=last.end,
                    color_id=first.color_id,
                    color_hex=first.color_hex,
                    summary=first.summary,
                    private_metadata=metadata,
                )
            )
    return compressed, len(bands)


def build_single_frame_plan(
    manifest: AnimationManifest,
    profile: CalibrationProfile,
    frame_index: int,
    anchor_date: date,
    run_id: str,
    max_execute_events: int,
    fit: FitMode = "contain",
    calendar_name: str = "Calendar Animation Lab",
    mapping_mode: FrameMappingMode = FrameMappingMode.FULL_GRID,
    event_compression: EventCompressionMode = DEFAULT_EVENT_COMPRESSION,
    calendar_background_color_id: str | None = None,
    subcolumn_order_strategy: str | SubcolumnOrderStrategy | None = None,
) -> SingleFrameCalendarPlan:
    if fit != "contain":
        raise CalendarAnimError(f"Unsupported frame fit: {fit}")
    if max_execute_events <= 0:
        raise CalendarAnimError("max execute events must be positive")
    if (
        event_compression is EventCompressionMode.SYNCHRONIZED_HORIZONTAL_BANDS
        and mapping_mode is not FrameMappingMode.FULL_GRID
    ):
        raise CalendarAnimError(
            "Synchronized horizontal-band compression requires full-grid mapping"
        )
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
    expanded = expand_frame_blocks(
        frame,
        manifest.render.grid_width,
        manifest.render.grid_height,
        manifest.render.background,
    )
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
            "Horizontal strategy is not supported by the single-frame mapper: "
            f"{horizontal_strategy}"
        )

    background_color = calendar_palette_color(calendar_background_color_id)
    ordering_strategy = parse_subcolumn_order_strategy(
        subcolumn_order_strategy
        or (
            SubcolumnOrderStrategy.SUMMARY_PREFIX
            if mapping_mode is FrameMappingMode.FULL_GRID
            else SubcolumnOrderStrategy.NONE
        )
    )
    mapping_cells = generate_mapping_cells(
        mapping_mode,
        fitted,
        target_width,
        target_height,
        background_color.hex,
    )
    contrast_background = (
        background_color.hex
        if mapping_mode is FrameMappingMode.FULL_GRID
        else manifest.render.background or DEFAULT_CALENDAR_BACKGROUND
    )
    mapped, events = map_cells_to_calendar(
        mapping_cells,
        profile,
        week_start_date,
        profile.calendar_ui.timezone,
        manifest.animation_id,
        run_id,
        frame_index,
        contrast_background,
        background_color.id if mapping_mode is FrameMappingMode.FULL_GRID else None,
        ordering_strategy,
    )
    baseline_event_count = len(events)
    synchronized_band_count = 0
    if event_compression is EventCompressionMode.SYNCHRONIZED_HORIZONTAL_BANDS:
        events, synchronized_band_count = compress_events_into_synchronized_horizontal_bands(
            mapped,
            events,
            target_width,
            target_height,
            profile.horizontal_mapping.usable_overlap_columns_per_day or 1,
            profile.horizontal_mapping.days_used,
        )

    profile_ready = profile.mapper_ready
    strategy_matches_profile = profile.subcolumn_order_mapping.strategy_ready(ordering_strategy)
    compression_matches_profile = (
        event_compression is EventCompressionMode.NONE
        or synchronized_horizontal_bands_ready(profile)
    )
    if mapping_mode is FrameMappingMode.FULL_GRID:
        profile_ready = profile_ready and strategy_matches_profile
    profile_ready = profile_ready and compression_matches_profile

    warnings: list[str] = []
    normalized_source_background = (
        manifest.render.background.upper() if manifest.render.background is not None else None
    )
    ignored_background_cells = sum(
        block.width * block.height
        for block in frame.blocks
        if normalized_source_background is not None
        and block.color_hex.upper() == normalized_source_background
    )
    if ignored_background_cells:
        warnings.append(
            f"Ignored {ignored_background_cells} legacy manifest cell(s) whose color matches "
            f"the configured source background {normalized_source_background}."
        )
    if mapping_mode is FrameMappingMode.FULL_GRID:
        warnings.append(
            "Full-grid keeps every calibrated logical cell in deterministic "
            "day/row/subcolumn order, but final visual ordering still depends on Google Calendar."
        )
        if not strategy_matches_profile:
            recommended = profile.subcolumn_order_mapping.recommended_slot_order_strategy or "none"
            warnings.append(
                f"Full-grid uses {ordering_strategy.value}, but the calibration profile does "
                f"not confirm that strategy (recommended: {recommended})."
            )
    else:
        warnings.append(
            "Sparse mapping cannot guarantee absolute subcolumn positions because Calendar "
            "controls the layout of simultaneous events."
        )
        if calendar_background_color_id is not None:
            warnings.append("Calendar background color is ignored in sparse mode.")
    if event_compression is EventCompressionMode.SYNCHRONIZED_HORIZONTAL_BANDS:
        warnings.append(
            "Calendar events are compressed into vertically synchronized six-slot bands; "
            "the logical preview remains a complete full-grid canvas."
        )
        if not compression_matches_profile:
            warnings.append(
                "Synchronized horizontal-band compression is not approved by the loaded "
                "calibration profile."
            )
    if not profile_ready:
        blockers = list(profile.missing_mapper_calibrations)
        if mapping_mode is FrameMappingMode.FULL_GRID and not strategy_matches_profile:
            blockers.append(f"confirmed {ordering_strategy.value} mapper strategy")
        if not compression_matches_profile:
            blockers.append("synchronized horizontal-bands calibration")
        missing = ", ".join(blockers)
        warnings.append(
            "Calibration profile is NOT READY; dry-run is allowed but real upload is blocked. "
            f"Missing: {missing}."
        )
    if recorded_strategy is None:
        warnings.append("Horizontal strategy is uncalibrated; this dry-run uses unit-cells-only.")
    if len(events) > max_execute_events:
        warnings.append(
            f"Event count {len(events)} exceeds the configured execute limit {max_execute_events}."
        )

    foreground_mapped = [cell for cell in mapped if cell.cell_role is CellRole.FOREGROUND]
    background_count = len(mapped) - len(foreground_mapped)
    event_count = len(events)
    foreground_event_count = sum(
        event.private_metadata.get("cell_role") == CellRole.FOREGROUND.value for event in events
    )
    background_event_count = sum(
        event.private_metadata.get("cell_role") == CellRole.BACKGROUND.value for event in events
    )
    mapped_count = len(mapped)
    return SingleFrameCalendarPlan(
        animation_id=manifest.animation_id,
        run_id=run_id,
        calendar_name=calendar_name,
        frame_index=frame_index,
        timezone=profile.calendar_ui.timezone,
        week_start_date=week_start_date,
        source_grid_width=manifest.render.grid_width,
        source_grid_height=manifest.render.grid_height,
        target_grid_width=target_width,
        target_grid_height=target_height,
        columns_per_day=(profile.horizontal_mapping.usable_overlap_columns_per_day or 1),
        days_used=profile.horizontal_mapping.days_used,
        fit=fit,
        mapping_mode=mapping_mode,
        event_compression=event_compression,
        background_color_id=(
            background_color.id if mapping_mode is FrameMappingMode.FULL_GRID else None
        ),
        profile_ready=profile_ready,
        horizontal_strategy=horizontal_strategy,
        subcolumn_order_strategy=ordering_strategy,
        subcolumn_order_keys=summary_order_keys(
            profile.horizontal_mapping.usable_overlap_columns_per_day or 1,
            ordering_strategy,
        ),
        max_execute_events=max_execute_events,
        warnings=warnings,
        statistics=FrameMappingStatistics(
            source_blocks=len(frame.blocks),
            expanded_logical_cells=len(expanded),
            non_background_cells=len(expanded),
            mapped_cells=mapped_count,
            calendar_events=event_count,
            unique_calendar_colors=len({cell.color_id for cell in mapped}),
            cells_per_event=(mapped_count / event_count if event_count else 0),
            compression_ratio=(event_count / mapped_count if mapped_count else 0),
            foreground_cells_after_fitting=len(foreground_mapped),
            background_structural_cells=background_count,
            total_logical_cells=mapped_count,
            foreground_events=foreground_event_count,
            background_events=background_event_count,
            foreground_calendar_colors=len({cell.color_id for cell in foreground_mapped}),
            sparse_event_estimate=len(fitted),
            full_grid_event_estimate=target_width * target_height,
            baseline_calendar_events=baseline_event_count,
            saved_calendar_events=baseline_event_count - event_count,
            synchronized_horizontal_bands=synchronized_band_count,
        ),
        mapped_cells=mapped,
        events=events,
    )
