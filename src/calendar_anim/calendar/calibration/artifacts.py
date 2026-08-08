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
    if plan.pattern == "color-palette":
        return _build_color_report(plan, executed)
    if plan.pattern == "position-grid":
        return _build_position_report(plan, executed)
    if plan.pattern == "horizontal-bars":
        return _build_horizontal_bars_report(plan, executed)
    if plan.pattern == "subcolumn-order":
        return _build_subcolumn_order_report(plan, executed)

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


def _build_color_report(plan: CalibrationPlan, executed: bool) -> str:
    lines = [
        "Color Palette Calibration",
        "=========================",
        "",
        f"Run ID: {plan.run_id}",
        f"Start date: {plan.start_date.isoformat()}",
        f"Timezone: {plan.timezone}",
        f"Events: {plan.event_count}",
        f"Execution: {'REAL' if executed else 'DRY RUN'}",
        "",
        "Tested colors",
        "-------------",
    ]
    for event in plan.events:
        lines.extend(
            [
                f"Color ID: {event.color_id}",
                f"  Logical name: {event.private_metadata['logical_color_name']}",
                f"  Approximate hex: {event.private_metadata['color_hex_approx']}",
                f"  Time: {event.start:%Y-%m-%d %H:%M}-{event.end:%H:%M}",
            ]
        )
    lines.extend(
        [
            "",
            "Observed results",
            "----------------",
            "- Preferred color IDs:",
            "- Recommended color count:",
            "- Poor contrast color IDs:",
            "- Similar color groups:",
            "- Notes:",
            "",
            "Approximate hex values are internal references, not exact browser-rendered colors.",
            "",
        ]
    )
    return "\n".join(lines)


def _build_position_report(plan: CalibrationPlan, executed: bool) -> str:
    lines = [
        "Position Grid Calibration",
        "=========================",
        "",
        f"Run ID: {plan.run_id}",
        f"Start date: {plan.start_date.isoformat()}",
        f"Timezone: {plan.timezone}",
        f"Events: {plan.event_count}",
        f"Execution: {'REAL' if executed else 'DRY RUN'}",
        "",
        "Known positions",
        "---------------",
    ]
    for event in plan.events:
        lines.append(
            f"- {event.summary}: day={event.private_metadata['logical_day']}, "
            f"row={event.private_metadata['logical_row']}, start={event.start.isoformat()}"
        )
    lines.extend(
        [
            "",
            "Observed results",
            "----------------",
            "- Week alignment OK:",
            "- Timezone alignment OK:",
            "- Day alignment OK:",
            "- Vertical alignment OK:",
            "- Week starts on:",
            "- Visible range:",
            "- Notes:",
            "",
        ]
    )
    return "\n".join(lines)


def _build_horizontal_bars_report(plan: CalibrationPlan, executed: bool) -> str:
    groups: dict[str, list[CalendarEventDraft]] = defaultdict(list)
    for event in plan.events:
        groups[event.private_metadata["group"]].append(event)
    lines = [
        "Horizontal Bars Calibration",
        "===========================",
        "",
        f"Run ID: {plan.run_id}",
        f"Start date: {plan.start_date.isoformat()}",
        f"Timezone: {plan.timezone}",
        f"Events: {plan.event_count}",
        f"Execution: {'REAL' if executed else 'DRY RUN'}",
        "Strategy: independent-cells",
        "Partial internal positioning: not tested by this pattern",
        "",
        "Test bars",
        "---------",
    ]
    for width in range(1, 7):
        events = groups[f"bar-{width}"]
        lines.extend(
            [
                f"bar-{width}",
                f"  Time: {events[0].start:%H:%M}-{events[0].end:%H:%M}",
                f"  Cells: {len(events)}",
                f"  Shared color ID: {events[0].color_id}",
                "  Visually contiguous:",
                "  Visible gaps:",
                f"  Expected logical width: {width}",
            ]
        )
    lines.extend(
        [
            "",
            "Observed results",
            "----------------",
            "- Independent cells appear contiguous:",
            "- Visible gaps between cells:",
            "- Same-color cells merge visually:",
            "- Maximum useful bar width:",
            "- Partial bar positioning predictable: not tested / unknown",
            "- Recommended horizontal strategy:",
            "- Notes:",
            "",
            "This experiment does not assert that block.width equals simultaneous events.",
            "",
        ]
    )
    return "\n".join(lines)


def _build_subcolumn_order_report(plan: CalibrationPlan, executed: bool) -> str:
    groups: dict[str, list[CalendarEventDraft]] = defaultdict(list)
    for event in plan.events:
        groups[event.private_metadata["group"]].append(event)
    lines = [
        "Subcolumn Order Calibration",
        "===========================",
        "",
        f"Run ID: {plan.run_id}",
        f"Calendar: {plan.calendar_name}",
        f"Start date: {plan.start_date.isoformat()}",
        f"Timezone: {plan.timezone}",
        f"Events: {plan.event_count}",
        f"Execution: {'REAL' if executed else 'DRY RUN'}",
        "",
        "Creation-order groups",
        "---------------------",
    ]
    for group_name, events in groups.items():
        lines.extend(
            [
                group_name,
                f"  Variant: {events[0].private_metadata['variant']}",
                f"  Time: {events[0].start:%H:%M}-{events[0].end:%H:%M}",
                "  Creation order: " + " ".join(event.summary for event in events),
                "  Slot indexes: "
                + " ".join(event.private_metadata["subcolumn_index"] for event in events),
            ]
        )
    lines.extend(
        [
            "",
            "This is the expected logical creation order, not a guarantee of Google Calendar "
            "visual ordering.",
            "",
            "Observed visual order",
            "---------------------",
            "",
            "Forward group 1:",
            "Visual order:",
            "",
            "Forward group 2:",
            "Visual order:",
            "",
            "Reverse group:",
            "Visual order:",
            "",
            "Shuffled group:",
            "Visual order:",
            "",
            "After browser refresh:",
            "Visual order stable: yes/no",
            "",
            "After navigating away and back:",
            "Visual order stable: yes/no",
            "",
            "After reopening Calendar:",
            "Visual order stable: yes/no",
            "",
            "Does creation order influence visual order:",
            "yes/no/uncertain",
            "",
            "Recommended ordering strategy:",
            "",
            "Notes:",
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
    preview_titles = {
        "overlap-columns": "Overlap Columns Calibration",
        "color-palette": "Color Palette Calibration",
        "position-grid": "Position Grid Calibration",
        "horizontal-bars": "Horizontal Bars Calibration",
        "subcolumn-order": "Subcolumn Order Calibration",
    }
    if plan.pattern in preview_titles:
        draw.text((15, 12), preview_titles[plan.pattern], fill="black", font=font)
        draw.text(
            (15, 30),
            "LOGICAL EXPECTATION ONLY - verify the real result manually in Google Calendar",
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
        if (
            plan.pattern in {"overlap-columns", "horizontal-bars", "subcolumn-order"}
            and group_name not in labeled_groups
        ):
            draw.text((8, round(y1) + 2), group_name, fill="#424242", font=font)
            labeled_groups.add(group_name)
        fill = event.color_hex or "#4285F4"
        draw.rectangle((round(x1), round(y1), round(x2), round(y2)), fill=fill, outline="black")
        if x2 - x1 >= 18 and y2 - y1 >= 10:
            draw.text((round(x1) + 2, round(y1) + 1), event.summary, fill="white", font=font)
    if plan.pattern in preview_titles:
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
