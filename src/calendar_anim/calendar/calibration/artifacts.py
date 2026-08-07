from collections import defaultdict
from pathlib import Path

import yaml
from PIL import Image, ImageDraw, ImageFont

from calendar_anim.calendar.calibration.models import (
    CalibrationExecutionResult,
    CalibrationObservations,
    CalibrationPlan,
)
from calendar_anim.calendar.models import CalendarEventDraft


def write_plan(plan: CalibrationPlan, output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "calibration-plan.json"
    path.write_text(plan.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return path


def write_execution_result(result: CalibrationExecutionResult, output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "execution-result.json"
    path.write_text(result.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return path


def build_report(plan: CalibrationPlan, executed: bool) -> str:
    if plan.pattern == "overlap-columns":
        return _build_overlap_report(plan, executed)

    lines = [
        "Calendar Animation Calibration",
        "==============================",
        "",
        f"Pattern: {plan.pattern}",
        f"Animation ID: {plan.animation_id}",
        f"Run ID: {plan.run_id}",
        f"Start date: {plan.start_date.isoformat()}",
        f"Timezone: {plan.timezone}",
        f"Calendar: {plan.calendar_name}",
        f"Events: {plan.event_count}",
        f"Limit: {plan.max_events}",
        f"Execution: {'REAL' if executed else 'DRY RUN'}",
        "",
        "Groups:",
    ]
    groups: dict[str, list[CalendarEventDraft]] = defaultdict(list)
    for event in plan.events:
        groups[event.private_metadata.get("group", "ungrouped")].append(event)
    for group, events in groups.items():
        first = events[0]
        durations = sorted(
            {round((event.end - event.start).total_seconds() / 60) for event in events}
        )
        duration_text = ", ".join(f"{duration}m" for duration in durations)
        lines.append(
            f"- {group}: {len(events)} event(s), {first.start:%Y-%m-%d %H:%M}, "
            f"duration(s) {duration_text}"
        )
    lines.extend(
        [
            "",
            "The expected layout is a logical preview and may differ from Google "
            "Calendar's real overlap algorithm.",
            "Open Google Calendar in week view and record the measured UI behavior manually.",
            "",
        ]
    )
    return "\n".join(lines)


def _build_overlap_report(plan: CalibrationPlan, executed: bool) -> str:
    groups: dict[str, list[CalendarEventDraft]] = defaultdict(list)
    for event in plan.events:
        groups[event.private_metadata.get("group", "ungrouped")].append(event)

    lines = [
        "Overlap Columns Calibration",
        "===========================",
        "",
        f"Run ID: {plan.run_id}",
        f"Start date: {plan.start_date.isoformat()}",
        f"Timezone: {plan.timezone}",
        f"Calendar: {plan.calendar_name}",
        f"Events: {plan.event_count}",
        f"Execution: {'REAL' if executed else 'DRY RUN'}",
        "",
        "Target UI conditions",
        "--------------------",
        "- View: week",
        "- Browser zoom: 100%",
        "- Target viewport: 1920x1080",
        "- Sidebar: hidden",
        "- Weekends: visible",
        "- Visible hours: 06:00-18:00",
        "",
        "Test groups",
        "-----------",
    ]
    ordered_groups = sorted(
        groups.items(),
        key=lambda item: int(item[0].rsplit("-", maxsplit=1)[-1]),
    )
    for group, events in ordered_groups:
        first = events[0]
        lines.extend(
            [
                f"{group}",
                f"  Time: {first.start:%H:%M}-{first.end:%H:%M}",
                f"  Events: {len(events)}",
                "  Expected: simultaneous side-by-side columns",
            ]
        )

    lines.extend(
        [
            "",
            "Observed results (fill in after opening Google Calendar)",
            "--------------------------------------------------------",
        ]
    )
    for size in range(1, 7):
        lines.extend(
            [
                f"Group {size}:",
                "- visually separated:",
                "- similar widths:",
                "- partial overlap:",
                "- order predictable:",
                "- titles readable:",
                "- colors distinguishable:",
                "- usable as a pixel:",
                "- notes:",
                "",
            ]
        )
    lines.extend(
        [
            "Maximum tested overlap columns: ______",
            "Recommended usable overlap columns per day: ______",
            "Decision priority: visual separation (title readability is secondary).",
            "Notes: ______________________________________________",
            "",
            "WARNING: expected-layout.png is a logical expectation only; it is not a",
            "simulation of Google Calendar's real overlap layout algorithm.",
            "",
        ]
    )
    return "\n".join(lines)


def write_report(plan: CalibrationPlan, output_dir: Path, executed: bool = False) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "calibration-report.txt"
    path.write_text(build_report(plan, executed), encoding="utf-8")
    return path


def write_expected_layout(plan: CalibrationPlan, output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    width, height = 1400, 900
    left, top, right, bottom = 130, 90, 30, 55
    grid_width = width - left - right
    grid_height = height - top - bottom
    day_width = grid_width / 7
    start_hour, end_hour = 6, 18
    hour_height = grid_height / (end_hour - start_hour)
    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default()
    if plan.pattern == "overlap-columns":
        draw.text((15, 12), "Overlap Columns Calibration", fill="black", font=font)
        draw.text(
            (15, 30),
            "LOGICAL EXPECTATION ONLY - not Google Calendar's real overlap algorithm",
            fill="#B3261E",
            font=font,
        )
    day_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    for day in range(8):
        x = round(left + day * day_width)
        draw.line((x, top, x, height - bottom), fill="#BDBDBD", width=1)
        if day < 7:
            draw.text((x + 6, 62), day_names[day], fill="black", font=font)
    for hour in range(start_hour, end_hour + 1):
        y = round(top + (hour - start_hour) * hour_height)
        draw.line((left, y, width - right, y), fill="#E0E0E0", width=1)
        draw.text((82, y - 6), f"{hour:02d}:00", fill="#424242", font=font)
    simultaneous: dict[tuple[str, str], list[int]] = defaultdict(list)
    labeled_groups: set[str] = set()
    for index, event in enumerate(plan.events):
        simultaneous[(event.start.isoformat(), event.end.isoformat())].append(index)
    for index, event in enumerate(plan.events):
        day = (event.start.date() - plan.start_date).days
        if not 0 <= day < 7:
            continue
        group = simultaneous[(event.start.isoformat(), event.end.isoformat())]
        position = group.index(index)
        count = len(group)
        inner_width = (day_width - 8) / count
        x1 = left + day * day_width + 4 + position * inner_width
        x2 = x1 + inner_width - 2
        start_minutes = event.start.hour * 60 + event.start.minute
        end_minutes = event.end.hour * 60 + event.end.minute
        y1 = top + ((start_minutes / 60) - start_hour) * hour_height
        y2 = top + ((end_minutes / 60) - start_hour) * hour_height
        group_name = event.private_metadata.get("group", "ungrouped")
        if plan.pattern == "overlap-columns" and group_name not in labeled_groups:
            draw.text((8, round(y1) + 2), group_name, fill="#424242", font=font)
            labeled_groups.add(group_name)
        fill = event.color_hex or "#4285F4"
        draw.rectangle((round(x1), round(y1), round(x2), round(y2)), fill=fill, outline="black")
        if x2 - x1 >= 18 and y2 - y1 >= 10:
            draw.text((round(x1) + 2, round(y1) + 1), event.summary, fill="white", font=font)
    if plan.pattern == "overlap-columns":
        draw.text(
            (15, height - 28),
            "Preview only: verify actual placement manually in Google Calendar week view.",
            fill="#B3261E",
            font=font,
        )
    path = output_dir / "expected-layout.png"
    image.save(path)
    return path


def write_dry_run_artifacts(plan: CalibrationPlan, output_dir: Path) -> None:
    write_plan(plan, output_dir)
    write_report(plan, output_dir, executed=False)
    write_expected_layout(plan, output_dir)
    write_execution_result(
        CalibrationExecutionResult(
            executed=False,
            run_id=plan.run_id,
            animation_id=plan.animation_id,
            pattern=plan.pattern,
        ),
        output_dir,
    )


def write_observations(observations: CalibrationObservations, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(observations.model_dump(mode="json"), sort_keys=False), encoding="utf-8"
    )
