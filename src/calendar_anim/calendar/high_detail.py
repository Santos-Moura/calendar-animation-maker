from typing import Final

from calendar_anim.calendar.calibration.models import CalibrationProfile
from calendar_anim.exceptions import CalendarAnimError

HIGH_DETAIL_GRID: Final = "126x72"
HIGH_DETAIL_SLOTS_PER_DAY: Final = 18
HIGH_DETAIL_VERTICAL_STEP_MINUTES: Final = 15
HIGH_DETAIL_VISIBLE_START_HOUR: Final = 6
HIGH_DETAIL_VISIBLE_END_HOUR: Final = 24
HIGH_DETAIL_GRID_PROFILE: Final = f"high-detail-{HIGH_DETAIL_GRID}"
HIGH_DETAIL_EXPERIMENTAL_MAX_EVENTS: Final = 2500


def is_high_detail_geometry(
    grid_profile: str,
    width: int,
    height: int,
    slots_per_day: int | None,
    vertical_step_minutes: int | None,
    visible_start_hour: int | None,
    visible_end_hour: int | None,
) -> bool:
    """Return whether a persisted plan is the explicit 126x72 experiment."""

    return (
        grid_profile == HIGH_DETAIL_GRID_PROFILE
        and width == 126
        and height == 72
        and slots_per_day == HIGH_DETAIL_SLOTS_PER_DAY
        and vertical_step_minutes == HIGH_DETAIL_VERTICAL_STEP_MINUTES
        and visible_start_hour == HIGH_DETAIL_VISIBLE_START_HOUR
        and visible_end_hour == HIGH_DETAIL_VISIBLE_END_HOUR
    )


def apply_high_detail_grid(
    base_profile: CalibrationProfile,
    grid: str,
) -> CalibrationProfile:
    """Return an isolated mapper profile for the validated high-detail candidate."""

    if grid.lower().strip() != HIGH_DETAIL_GRID:
        raise CalendarAnimError(
            f"Unsupported experimental grid {grid!r}; supported: {HIGH_DETAIL_GRID}"
        )
    data = base_profile.model_dump()
    data["horizontal_mapping"]["maximum_tested_overlap_columns"] = HIGH_DETAIL_SLOTS_PER_DAY
    data["horizontal_mapping"]["usable_overlap_columns_per_day"] = HIGH_DETAIL_SLOTS_PER_DAY
    data["vertical_mapping"]["minimum_distinguishable_height_minutes"] = (
        HIGH_DETAIL_VERTICAL_STEP_MINUTES
    )
    for section in ("calendar_ui", "position_mapping"):
        data[section]["visible_start_hour"] = HIGH_DETAIL_VISIBLE_START_HOUR
        data[section]["visible_end_hour"] = HIGH_DETAIL_VISIBLE_END_HOUR
    return CalibrationProfile.model_validate(data)
