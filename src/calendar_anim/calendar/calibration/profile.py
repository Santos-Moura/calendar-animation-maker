from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from calendar_anim.calendar.calibration.models import CalibrationObservations, CalibrationProfile

DEFAULT_PROFILE_PATH = Path("output/calibration/calibration-profile.yaml")


def load_profile(path: Path) -> CalibrationProfile:
    if not path.exists():
        return CalibrationProfile()
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return CalibrationProfile.model_validate(raw)


def save_profile(profile: CalibrationProfile, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = profile.model_dump(mode="json", exclude_none=False)
    path.write_text(
        yaml.safe_dump(payload, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    return path


def load_observations(path: Path) -> CalibrationObservations:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return CalibrationObservations.model_validate(raw)


def apply_observations(
    profile: CalibrationProfile,
    recorded: CalibrationObservations,
) -> CalibrationProfile:
    ui = recorded.calendar_ui
    for field in (
        "view",
        "timezone",
        "browser_zoom_percent",
        "viewport_width",
        "viewport_height",
        "sidebar_visible",
        "weekends_visible",
        "visible_start_hour",
        "visible_end_hour",
    ):
        value = getattr(ui, field)
        if value is not None:
            setattr(profile.calendar_ui, field, value)

    values = recorded.observations
    if values.minimum_visible_event_minutes is not None:
        profile.vertical_mapping.minimum_visible_event_minutes = (
            values.minimum_visible_event_minutes
        )
    if values.minimum_distinguishable_height_minutes is not None:
        profile.vertical_mapping.minimum_distinguishable_height_minutes = (
            values.minimum_distinguishable_height_minutes
        )
    if values.maximum_tested_overlap_columns is not None:
        profile.horizontal_mapping.maximum_tested_overlap_columns = (
            values.maximum_tested_overlap_columns
        )
    if values.usable_overlap_columns is not None:
        profile.horizontal_mapping.usable_overlap_columns_per_day = values.usable_overlap_columns

    # Re-validate so the derived row/column counts are refreshed after mutation.
    return CalibrationProfile.model_validate(profile.model_dump())


def profile_summary(profile: CalibrationProfile) -> str:
    ui = profile.calendar_ui
    vertical = profile.vertical_mapping
    horizontal = profile.horizontal_mapping

    viewport = "pending"
    if ui.viewport_width is not None and ui.viewport_height is not None:
        viewport = f"{ui.viewport_width}x{ui.viewport_height}"
    zoom = f"{ui.browser_zoom_percent}%" if ui.browser_zoom_percent is not None else "pending"

    lines = [
        "Calendar calibration summary",
        f"UI: {ui.view} view, {ui.timezone}, zoom {zoom}, viewport {viewport}",
        (
            "Visible window: "
            f"{ui.visible_start_hour:02d}:00-{ui.visible_end_hour:02d}:00 "
            f"({(ui.visible_end_hour - ui.visible_start_hour) * 60} minutes)"
        ),
        "",
        "Vertical mapping",
        f"  Minimum visible event: {_minutes(vertical.minimum_visible_event_minutes)}",
        (
            "  Minimum distinguishable height: "
            f"{_minutes(vertical.minimum_distinguishable_height_minutes)}"
        ),
        f"  Logical rows: {_value(vertical.logical_rows)}",
        "",
        "Horizontal mapping",
        (f"  Maximum tested overlap columns: {_value(horizontal.maximum_tested_overlap_columns)}"),
        (
            "  Usable overlaps per day: "
            f"{_horizontal_value(horizontal.usable_overlap_columns_per_day)}"
        ),
        f"  Days used: {horizontal.days_used}",
        f"  Logical columns: {_logical_columns(horizontal.logical_columns)}",
        "",
        (
            "Candidate logical grid: "
            f"{_candidate_grid(horizontal.logical_columns, vertical.logical_rows)}"
        ),
    ]
    return "\n".join(lines)


def _minutes(value: int | None) -> str:
    return f"{value} minutes" if value is not None else "pending"


def _value(value: Any | None) -> str:
    return str(value) if value is not None else "pending"


def _horizontal_value(value: int | None) -> str:
    return str(value) if value is not None else "not measured yet"


def _logical_columns(value: int | None) -> str:
    return str(value) if value is not None else "pending overlap-columns calibration"


def _candidate_grid(columns: int | None, rows: int | None) -> str:
    if columns is None or rows is None:
        return "pending"
    return f"{columns}x{rows}"
