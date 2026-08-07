from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from calendar_anim.calendar.frame_mapping.models import (
    SingleFrameCalendarPlan,
    SingleFrameExecutionResult,
)
from calendar_anim.exceptions import CalendarAnimError


def build_mapping_report(plan: SingleFrameCalendarPlan) -> str:
    stats = plan.statistics
    lines = [
        "Single Frame Calendar Mapping",
        "=============================",
        "",
        f"Animation ID: {plan.animation_id}",
        f"Run ID: {plan.run_id}",
        f"Frame index: {plan.frame_index}",
        f"Week start: {plan.week_start_date}",
        f"Timezone: {plan.timezone}",
        f"Source grid: {plan.source_grid_width}x{plan.source_grid_height}",
        f"Target grid: {plan.target_grid_width}x{plan.target_grid_height}",
        f"Fit: {plan.fit}",
        f"Horizontal strategy: {plan.horizontal_strategy}",
        f"Calibration profile ready: {'yes' if plan.profile_ready else 'no'}",
        "",
        "Metrics",
        "-------",
        f"Source blocks: {stats.source_blocks}",
        f"Expanded logical cells: {stats.expanded_logical_cells}",
        f"Non-background cells: {stats.non_background_cells}",
        f"Mapped cells: {stats.mapped_cells}",
        f"Calendar events: {stats.calendar_events}",
        f"Unique Calendar colors: {stats.unique_calendar_colors}",
        f"Cells per event: {stats.cells_per_event:.2f}",
        f"Compression ratio: {stats.compression_ratio:.2f}",
        f"Execute limit: {plan.max_execute_events}",
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
            "The preview is a logical mapping, not a simulation of Google Calendar CSS.",
            "One mapped cell equals one event in this first experiment.",
            "",
        ]
    )
    return "\n".join(lines)


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
    cell_size = 20
    left = 60
    top = 50
    width = left + plan.target_grid_width * cell_size + 20
    height = top + plan.target_grid_height * cell_size + 40
    image = Image.new("RGB", (width, height), "#202124")
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default()
    draw.text(
        (12, 10), f"Frame {plan.frame_index} logical Calendar mapping", fill="white", font=font
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
    columns_per_day = plan.target_grid_width // 7
    for x in range(plan.target_grid_width + 1):
        line_x = left + x * cell_size
        color = "#9AA0A6" if x % columns_per_day == 0 else "#3C4043"
        draw.line(
            (line_x, top, line_x, height - 40),
            fill=color,
            width=2 if x % columns_per_day == 0 else 1,
        )
    for day, label in enumerate(("Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat")):
        draw.text(
            (left + day * columns_per_day * cell_size + 4, top - 16),
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
