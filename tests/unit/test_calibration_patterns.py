from datetime import UTC, date, datetime

import pytest

from calendar_anim.calendar.calibration.patterns import (
    EVENT_COLORS,
    PATTERNS,
    build_calibration_plan,
    generate_run_id,
)
from calendar_anim.exceptions import CalendarAnimError

pytestmark = pytest.mark.unit

START = date(2026, 8, 10)
EXPECTED_COUNTS = {
    "duration-scale": 7,
    "overlap-columns": 21,
    "color-palette": 11,
    "position-grid": 6,
    "horizontal-bars": 15,
    "combined": 27,
}


@pytest.mark.parametrize(("pattern", "count"), EXPECTED_COUNTS.items())
def test_each_pattern_has_deterministic_event_count(pattern: str, count: int) -> None:
    plan = build_calibration_plan(pattern, START, run_id="test-run")  # type: ignore[arg-type]
    assert plan.event_count == count
    assert PATTERNS[pattern].approximate_events == count  # type: ignore[index]


def test_duration_scale_has_expected_durations_and_no_overlap() -> None:
    plan = build_calibration_plan("duration-scale", START, run_id="duration-run")
    durations = [round((event.end - event.start).total_seconds() / 60) for event in plan.events]
    assert durations == [5, 10, 15, 20, 30, 45, 60]
    assert all(
        left.end <= right.start for left, right in zip(plan.events, plan.events[1:], strict=False)
    )


def test_overlap_columns_has_simultaneous_members_and_separate_groups() -> None:
    plan = build_calibration_plan("overlap-columns", START, run_id="overlap-run")
    groups: dict[str, list[object]] = {}
    for event in plan.events:
        groups.setdefault(event.private_metadata["group"], []).append(event)
    assert [len(group) for group in groups.values()] == [1, 2, 3, 4, 5, 6]
    starts = []
    for group in groups.values():
        starts.append({event.start for event in group})  # type: ignore[attr-defined]
        assert len(starts[-1]) == 1
    assert len({next(iter(value)) for value in starts}) == 6


def test_color_palette_covers_supported_colors() -> None:
    plan = build_calibration_plan("color-palette", START, run_id="colors-run")
    assert [(event.color_id, event.color_hex) for event in plan.events] == EVENT_COLORS


def test_position_grid_uses_known_days_and_hours() -> None:
    plan = build_calibration_plan("position-grid", START, run_id="positions-run")
    assert [(event.start.weekday(), event.start.hour) for event in plan.events] == [
        (0, 8),
        (0, 15),
        (2, 8),
        (2, 15),
        (4, 8),
        (4, 15),
    ]


def test_horizontal_bars_are_explicitly_grouped() -> None:
    plan = build_calibration_plan("horizontal-bars", START, run_id="bars-run")
    assert [
        sum(event.private_metadata["group"] == f"bar-{size}" for event in plan.events)
        for size in range(1, 6)
    ] == [1, 2, 3, 4, 5]


def test_limit_is_enforced_and_can_be_explicitly_increased() -> None:
    with pytest.raises(CalendarAnimError, match="requires 21 events"):
        build_calibration_plan("overlap-columns", START, max_events=20, run_id="limit-run")
    assert (
        build_calibration_plan(
            "overlap-columns", START, max_events=21, run_id="increased-limit"
        ).event_count
        == 21
    )


def test_absolute_limit_is_enforced() -> None:
    with pytest.raises(CalendarAnimError, match="between 1 and 100"):
        build_calibration_plan("duration-scale", START, max_events=101)


def test_identifiers_metadata_timezone_and_intervals() -> None:
    plan = build_calibration_plan(
        "overlap-columns",
        START,
        timezone="America/Sao_Paulo",
        run_id="20260810T120000Z",
    )
    assert plan.animation_id == "calibration-overlap-columns"
    assert plan.run_id == "20260810T120000Z"
    for index, event in enumerate(plan.events):
        assert event.start.tzinfo is not None
        assert event.start < event.end
        assert (
            event.private_metadata
            | {
                "animation_id": "calibration-overlap-columns",
                "run_id": "20260810T120000Z",
                "pattern": "overlap-columns",
                "event_index": str(index),
                "generated_by": "calendar-anim",
            }
            == event.private_metadata
        )


def test_run_id_uses_injected_utc_clock() -> None:
    now = datetime(2026, 8, 10, 12, 0, 0, 123456, tzinfo=UTC)
    assert generate_run_id(now) == "20260810T120000123456Z"


def test_plan_serialization_contains_computed_event_count() -> None:
    plan = build_calibration_plan("duration-scale", START, run_id="serialized-run")
    payload = plan.model_dump_json()
    assert '"event_count":7' in payload
