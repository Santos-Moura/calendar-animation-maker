from datetime import timedelta
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from calendar_anim.calendar.frame_mapping.models import (
    CalendarMappedCell,
    SingleFrameCalendarPlan,
    SingleFrameExecutionResult,
)
from calendar_anim.exceptions import CalendarAnimError


def build_mapping_report(plan: SingleFrameCalendarPlan) -> str:
    stats = plan.statistics
    difference = stats.full_grid_event_estimate - stats.sparse_event_estimate
    background_color = plan.background_color_id or "not used"
    lines = [
        "Single Frame Calendar Mapping",
        "=============================",
        "",
        f"Animation ID: {plan.animation_id}",
        f"Run ID: {plan.run_id}",
        f"Frame index: {plan.frame_index}",
        f"Mapping mode: {plan.mapping_mode.value}",
        f"Week start: {plan.week_start_date}",
        f"Timezone: {plan.timezone}",
        f"Source grid: {plan.source_grid_width}x{plan.source_grid_height}",
        f"Target grid: {plan.target_grid_width}x{plan.target_grid_height}",
        f"Columns per day: {plan.columns_per_day}",
        f"Rows: {plan.target_grid_height}",
        f"Fit: {plan.fit}",
        f"Horizontal strategy: {plan.horizontal_strategy}",
        "Submission order key: day_offset, logical_y, subcolumn_index",
        f"Background colorId: {background_color}",
        f"Calibration profile ready: {'yes' if plan.profile_ready else 'no'}",
        "",
        "Subcolumn ordering",
        "------------------",
        f"Strategy: {plan.subcolumn_order_strategy.value}",
        f"Columns per day: {plan.columns_per_day}",
        "Slot keys:",
        *_slot_key_lines(plan),
        "Ordering source: Empirical Google Calendar calibration",
        (
            "Warning: Google Calendar overlap ordering is not part of a documented API "
            "contract; this strategy is based on observed behavior."
        ),
        "",
        "Metrics",
        "-------",
        f"Source blocks: {stats.source_blocks}",
        f"Expanded source cells: {stats.expanded_logical_cells}",
        f"Foreground cells after fitting: {stats.foreground_cells_after_fitting}",
        f"Background structural cells: {stats.background_structural_cells}",
        f"Total logical cells: {stats.total_logical_cells}",
        f"Calendar events: {stats.calendar_events}",
        f"Foreground events: {stats.foreground_events}",
        f"Background events: {stats.background_events}",
        f"Calendar colors used for foreground: {stats.foreground_calendar_colors}",
        f"Cells per event: {stats.cells_per_event:.2f}",
        f"Compression ratio: {stats.compression_ratio:.2f}",
        f"Execute limit: {plan.max_execute_events}",
        "",
        "Mode comparison",
        "---------------",
        f"Sparse estimate: {stats.sparse_event_estimate} events",
        f"Full-grid estimate: {stats.full_grid_event_estimate} events",
        f"Difference: +{difference} events",
        "",
        "Ordering sample",
        "---------------",
        *_row_ordering_sample(plan),
        "",
        "Warnings",
        "--------",
    ]
    lines.extend(f"- {warning}" for warning in plan.warnings)
    if not plan.warnings:
        lines.append("- none")
    lines.extend(
        [
            "",
            "The preview is a logical pixel canvas, not a simulation of Google Calendar CSS.",
            "Calendar renders event colors differently in light and dark themes.",
            "One mapped cell equals one event in this baseline experiment.",
            "",
        ]
    )
    return "\n".join(lines)


def _row_ordering_sample(plan: SingleFrameCalendarPlan) -> list[str]:
    grouped: dict[tuple[int, int], list[CalendarMappedCell]] = {}
    for cell in plan.mapped_cells:
        grouped.setdefault((cell.day_offset, cell.logical_y), []).append(cell)
    if not grouped:
        return ["- no mapped cells"]
    selected_key = next(
        (
            key
            for key, cells in grouped.items()
            if any(cell.cell_role.value == "foreground" for cell in cells)
        ),
        next(iter(grouped)),
    )
    cells = grouped[selected_key]
    day_offset, row = selected_key
    lines = [f"day_offset={day_offset} row={row}"]
    events_by_position = {
        (
            int(event.private_metadata["day_offset"]),
            int(event.private_metadata["logical_y"]),
            int(event.private_metadata["subcolumn_index"]),
        ): event
        for event in plan.events
    }
    for cell in cells:
        event = events_by_position[(cell.day_offset, cell.logical_y, cell.subcolumn)]
        lines.append(
            f'subcolumn={cell.subcolumn} summary="{event.summary}" '
            f"colorId={cell.color_id} role={cell.cell_role.value}"
        )
    return lines


def _slot_key_lines(plan: SingleFrameCalendarPlan) -> list[str]:
    if not plan.subcolumn_order_keys:
        return ["- not used"]
    return [f"{subcolumn} -> {key}" for subcolumn, key in enumerate(plan.subcolumn_order_keys)]


def write_frame_mapping_artifacts(
    plan: SingleFrameCalendarPlan,
    source_image: Path,
    output_dir: Path,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "frame-plan.json").write_text(
        plan.model_dump_json(indent=2) + "\n", encoding="utf-8"
    )
    (output_dir / "mapping-report.txt").write_text(build_mapping_report(plan), encoding="utf-8")
    _write_source_frame(source_image, output_dir / "source-frame.png")
    _write_mapped_preview(plan, output_dir / "mapped-preview.png")
    _write_mapped_debug(plan, output_dir / "mapped-debug.png")
    write_frame_execution_result(
        SingleFrameExecutionResult(
            executed=False,
            run_id=plan.run_id,
            animation_id=plan.animation_id,
            frame_index=plan.frame_index,
            planned_events=plan.event_count,
        ),
        output_dir,
    )


def write_frame_execution_result(result: SingleFrameExecutionResult, output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "execution-result.json"
    path.write_text(result.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return path


def _write_source_frame(source: Path, destination: Path) -> None:
    try:
        with Image.open(source) as image:
            image.convert("RGB").save(destination)
    except (OSError, ValueError) as error:
        raise CalendarAnimError(f"Unable to read source frame image: {source}") from error


def _write_mapped_preview(plan: SingleFrameCalendarPlan, path: Path) -> None:
    """Write only the solid logical canvas, without Calendar-like decorations."""

    cell_size = 20
    image = Image.new(
        "RGB",
        (plan.target_grid_width * cell_size, plan.target_grid_height * cell_size),
        "#202124",
    )
    draw = ImageDraw.Draw(image)
    for cell in plan.mapped_cells:
        x1 = cell.logical_x * cell_size
        y1 = cell.logical_y * cell_size
        draw.rectangle(
            (x1, y1, x1 + cell_size - 1, y1 + cell_size - 1),
            fill=cell.color_hex,
        )
    image.save(path)


def _write_mapped_debug(plan: SingleFrameCalendarPlan, path: Path) -> None:
    """Write a diagnostic grid with day and subcolumn boundaries."""

    cell_size = 20
    left = 60
    top = 50
    width = left + plan.target_grid_width * cell_size + 20
    height = top + plan.target_grid_height * cell_size + 40
    image = Image.new("RGB", (width, height), "#202124")
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default()
    draw.text(
        (12, 10),
        f"Frame {plan.frame_index} {plan.mapping_mode.value} debug mapping",
        fill="white",
        font=font,
    )
    draw.text(
        (12, 26),
        f"{plan.source_grid_width}x{plan.source_grid_height} -> "
        f"{plan.target_grid_width}x{plan.target_grid_height}",
        fill="#BDC1C6",
        font=font,
    )
    for y in range(plan.target_grid_height + 1):
        line_y = top + y * cell_size
        draw.line((left, line_y, width - 20, line_y), fill="#3C4043")
    for x in range(plan.target_grid_width + 1):
        line_x = left + x * cell_size
        is_day_boundary = x % plan.columns_per_day == 0
        draw.line(
            (line_x, top, line_x, height - 40),
            fill="#9AA0A6" if is_day_boundary else "#3C4043",
            width=2 if is_day_boundary else 1,
        )
    for day in range(plan.days_used):
        label = (plan.week_start_date + timedelta(days=day)).strftime("%a")
        draw.text(
            (left + day * plan.columns_per_day * cell_size + 4, top - 16),
            label,
            fill="white",
            font=font,
        )
    for cell in plan.mapped_cells:
        x1 = left + cell.logical_x * cell_size + 1
        y1 = top + cell.logical_y * cell_size + 1
        draw.rectangle(
            (x1, y1, x1 + cell_size - 2, y1 + cell_size - 2),
            fill=cell.color_hex,
        )
    image.save(path)
