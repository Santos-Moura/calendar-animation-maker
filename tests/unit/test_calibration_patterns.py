from datetime import UTC, date, datetime

import pytest

from calendar_anim.calendar.calibration.patterns import (
    EVENT_COLOR_NAMES,
    EVENT_COLORS,
    PATTERNS,
    SUBCOLUMN_ORDER_COLORS,
    SUBCOLUMN_ORDER_VARIANTS,
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
    "position-grid": 9,
    "horizontal-bars": 21,
    "subcolumn-order": 24,
    "vertical-compression": 30,
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


def test_overlap_columns_has_exact_deterministic_group_structure() -> None:
    plan = build_calibration_plan("overlap-columns", START, run_id="overlap-run")
    groups: dict[str, list] = {}
    for event in plan.events:
        groups.setdefault(event.private_metadata["group"], []).append(event)

    assert list(groups) == [f"overlap-{size}" for size in range(1, 7)]
    assert [len(group) for group in groups.values()] == [1, 2, 3, 4, 5, 6]
    for size, (name, group) in enumerate(groups.items(), start=1):
        assert name == f"overlap-{size}"
        assert {event.start for event in group} == {group[0].start}
        assert {event.end for event in group} == {group[0].end}
        assert round((group[0].end - group[0].start).total_seconds() / 60) == 45
        assert [event.summary for event in group] == [
            f"{size}/{position}" for position in range(1, size + 1)
        ]
        assert [event.private_metadata["group_size"] for event in group] == [str(size)] * size
        assert [event.private_metadata["group_position"] for event in group] == [
            str(position) for position in range(1, size + 1)
        ]
        assert [(event.color_id, event.color_hex) for event in group] == EVENT_COLORS[:size]

    ordered_groups = list(groups.values())
    assert all(
        left[0].end <= right[0].start
        for left, right in zip(ordered_groups, ordered_groups[1:], strict=False)
    )


def test_color_palette_covers_supported_colors() -> None:
    plan = build_calibration_plan("color-palette", START, run_id="colors-run")
    assert [(event.color_id, event.color_hex) for event in plan.events] == EVENT_COLORS
    assert [event.private_metadata["color_id"] for event in plan.events] == [
        color_id for color_id, _ in EVENT_COLORS
    ]
    assert [event.private_metadata["logical_color_name"] for event in plan.events] == [
        EVENT_COLOR_NAMES[color_id] for color_id, _ in EVENT_COLORS
    ]
    assert all(round((event.end - event.start).total_seconds() / 60) == 40 for event in plan.events)
    assert all(
        left.end <= right.start or left.start.date() != right.start.date()
        for left, right in zip(plan.events, plan.events[1:], strict=False)
    )


def test_position_grid_uses_known_days_and_hours() -> None:
    plan = build_calibration_plan("position-grid", START, run_id="positions-run")
    assert [
        (event.start.weekday(), event.start.hour, event.start.minute) for event in plan.events
    ] == [
        (0, 6, 0),
        (0, 12, 0),
        (0, 17, 30),
        (2, 6, 0),
        (2, 12, 0),
        (2, 17, 30),
        (4, 6, 0),
        (4, 12, 0),
        (4, 17, 30),
    ]
    assert [event.summary for event in plan.events] == [
        "M-AM",
        "M-MID",
        "M-PM",
        "W-AM",
        "W-MID",
        "W-PM",
        "F-AM",
        "F-MID",
        "F-PM",
    ]
    assert [event.private_metadata["logical_day"] for event in plan.events] == [
        "monday",
        "monday",
        "monday",
        "wednesday",
        "wednesday",
        "wednesday",
        "friday",
        "friday",
        "friday",
    ]
    assert [event.private_metadata["logical_row"] for event in plan.events] == [
        "0",
        "12",
        "23",
        "0",
        "12",
        "23",
        "0",
        "12",
        "23",
    ]
    assert all(
        event.private_metadata["expected_start"] == event.start.isoformat() for event in plan.events
    )
    assert all(str(event.start.tzinfo) == "America/Sao_Paulo" for event in plan.events)


def test_horizontal_bars_are_explicitly_grouped() -> None:
    plan = build_calibration_plan("horizontal-bars", START, run_id="bars-run")
    assert [
        sum(event.private_metadata["group"] == f"bar-{size}" for event in plan.events)
        for size in range(1, 7)
    ] == [1, 2, 3, 4, 5, 6]
    groups = {
        size: [event for event in plan.events if event.private_metadata["group"] == f"bar-{size}"]
        for size in range(1, 7)
    }
    for size, events in groups.items():
        assert len({event.start for event in events}) == 1
        assert len({event.end for event in events}) == 1
        assert len({event.color_id for event in events}) == 1
        assert [event.private_metadata["bar_width"] for event in events] == [str(size)] * size
        assert [event.private_metadata["cell_position"] for event in events] == [
            str(position) for position in range(1, size + 1)
        ]
        assert {event.private_metadata["strategy"] for event in events} == {"independent-cells"}
    ordered = list(groups.values())
    assert all(
        left[0].end <= right[0].start for left, right in zip(ordered, ordered[1:], strict=False)
    )


def test_subcolumn_order_has_forward_reverse_and_shuffled_groups() -> None:
    plan = build_calibration_plan("subcolumn-order", START, run_id="slot-order-run")
    groups = [plan.events[index : index + 6] for index in range(0, 24, 6)]

    assert len(groups) == len(SUBCOLUMN_ORDER_VARIANTS) == 4
    for row_index, ((variant, expected_order), events) in enumerate(
        zip(SUBCOLUMN_ORDER_VARIANTS, groups, strict=True)
    ):
        assert len({event.start for event in events}) == 1
        assert len({event.end for event in events}) == 1
        assert [event.summary for event in events] == [f"S{slot}" for slot in expected_order]
        assert [event.private_metadata["subcolumn_index"] for event in events] == [
            str(slot) for slot in expected_order
        ]
        assert [event.private_metadata["creation_sequence"] for event in events] == [
            str(sequence) for sequence in range(6)
        ]
        assert {event.private_metadata["variant"] for event in events} == {variant}
        assert {event.private_metadata["row_index"] for event in events} == {str(row_index)}
        assert [(event.color_id, event.color_hex) for event in events] == [
            SUBCOLUMN_ORDER_COLORS[slot] for slot in expected_order
        ]

    assert [event.private_metadata["event_index"] for event in plan.events] == [
        str(index) for index in range(24)
    ]


def test_vertical_compression_has_control_compressed_mixed_and_staggered_groups() -> None:
    plan = build_calibration_plan("vertical-compression", START, run_id="vertical-run")
    groups: dict[str, list] = {}
    for event in plan.events:
        groups.setdefault(event.private_metadata["group"], []).append(event)

    assert list(groups) == [
        "vertical-control",
        "vertical-compressed",
        "vertical-mixed-length",
        "vertical-staggered",
    ]
    assert [len(events) for events in groups.values()] == [12, 6, 6, 6]
    assert {event.color_id for event in plan.events} == {"2"}
    assert {event.color_hex for event in plan.events} == {"#33B679"}

    control = groups["vertical-control"]
    assert [event.summary for event in control] == ["00", "01", "02"] * 4
    assert {round((event.end - event.start).total_seconds() / 60) for event in control} == {30}
    assert [event.private_metadata["segment_index"] for event in control] == [
        str(segment) for segment in range(4) for _ in range(3)
    ]

    compressed = groups["vertical-compressed"]
    assert [event.summary for event in compressed] == [f"{slot:02d}" for slot in range(6)]
    assert {event.start for event in compressed} == {compressed[0].start}
    assert {event.end for event in compressed} == {compressed[0].end}
    assert round((compressed[0].end - compressed[0].start).total_seconds() / 60) == 120

    mixed = groups["vertical-mixed-length"]
    assert [round((event.end - event.start).total_seconds() / 60) for event in mixed] == [
        30,
        60,
        90,
        120,
        90,
        60,
    ]
    assert {event.start for event in mixed} == {mixed[0].start}

    staggered = groups["vertical-staggered"]
    assert [(event.start.hour, event.start.minute) for event in staggered] == [
        (14, 0),
        (14, 30),
        (14, 0),
        (15, 0),
        (14, 0),
        (14, 30),
    ]
    assert all(
        event.summary == event.private_metadata["subcolumn_index"].zfill(2) for event in plan.events
    )


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
