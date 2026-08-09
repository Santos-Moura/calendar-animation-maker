from datetime import UTC, date, datetime, time, timedelta
from typing import Final
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from calendar_anim.calendar.calibration.models import (
    CalibrationPattern,
    CalibrationPlan,
    PatternDescription,
)
from calendar_anim.calendar.models import CalendarEventDraft
from calendar_anim.exceptions import CalendarAnimError

DEFAULT_CALENDAR_NAME: Final = "Calendar Animation Lab"
DEFAULT_MAX_EVENTS: Final = 30
ABSOLUTE_MAX_EVENTS: Final = 100

EVENT_COLORS: Final[list[tuple[str, str]]] = [
    ("1", "#7986CB"),
    ("2", "#33B679"),
    ("3", "#8E24AA"),
    ("4", "#E67C73"),
    ("5", "#F6BF26"),
    ("6", "#F4511E"),
    ("7", "#039BE5"),
    ("8", "#616161"),
    ("9", "#3F51B5"),
    ("10", "#0B8043"),
    ("11", "#D50000"),
]

EVENT_COLOR_NAMES: Final[dict[str, str]] = {
    "1": "lavender",
    "2": "sage",
    "3": "grape",
    "4": "flamingo",
    "5": "banana",
    "6": "tangerine",
    "7": "peacock",
    "8": "graphite",
    "9": "blueberry",
    "10": "basil",
    "11": "tomato",
}

SUBCOLUMN_ORDER_COLORS: Final[list[tuple[str, str]]] = [
    EVENT_COLORS[index] for index in (0, 1, 2, 4, 6, 10)
]
SUBCOLUMN_ORDER_VARIANTS: Final[list[tuple[str, list[int]]]] = [
    ("forward-1", [0, 1, 2, 3, 4, 5]),
    ("forward-2", [0, 1, 2, 3, 4, 5]),
    ("reverse", [5, 4, 3, 2, 1, 0]),
    ("shuffled", [2, 5, 0, 4, 1, 3]),
]
VERTICAL_COMPRESSION_COLOR: Final[tuple[str, str]] = EVENT_COLORS[1]

PATTERNS: Final[dict[CalibrationPattern, PatternDescription]] = {
    "duration-scale": PatternDescription(
        name="duration-scale",
        description="Compare event heights for different durations",
        approximate_events=7,
    ),
    "overlap-columns": PatternDescription(
        name="overlap-columns",
        description="Compare simultaneous event column layouts",
        approximate_events=21,
    ),
    "color-palette": PatternDescription(
        name="color-palette",
        description="Display available Calendar event colors",
        approximate_events=11,
    ),
    "position-grid": PatternDescription(
        name="position-grid",
        description="Test logical positions across days and times",
        approximate_events=9,
    ),
    "horizontal-bars": PatternDescription(
        name="horizontal-bars",
        description="Test adjacent horizontal logical blocks (experimental)",
        approximate_events=21,
    ),
    "subcolumn-order": PatternDescription(
        name="subcolumn-order",
        description="Test stable left-to-right ordering of six simultaneous events",
        approximate_events=24,
    ),
    "vertical-compression": PatternDescription(
        name="vertical-compression",
        description="Compare unit cells with vertically compressed mixed-duration columns",
        approximate_events=30,
    ),
    "combined": PatternDescription(
        name="combined",
        description="Small combined calibration suite",
        approximate_events=27,
    ),
}


def generate_run_id(now: datetime | None = None) -> str:
    current = now or datetime.now(UTC)
    current = current.astimezone(UTC)
    return current.strftime("%Y%m%dT%H%M%S%fZ")


def _event(
    day: date,
    hour: int,
    minute: int,
    duration_minutes: int,
    timezone: ZoneInfo,
    summary: str,
    group: str,
    color: tuple[str, str] | None = None,
    metadata: dict[str, str] | None = None,
) -> CalendarEventDraft:
    start = datetime.combine(day, time(hour, minute), timezone)
    private = {"group": group, **(metadata or {})}
    return CalendarEventDraft(
        start=start,
        end=start + timedelta(minutes=duration_minutes),
        color_id=color[0] if color else None,
        color_hex=color[1] if color else None,
        summary=summary,
        private_metadata=private,
    )


def _duration_scale(start: date, zone: ZoneInfo) -> list[CalendarEventDraft]:
    durations = [5, 10, 15, 20, 30, 45, 60]
    return [
        _event(
            start,
            8 + index,
            0,
            duration,
            zone,
            f"{duration}m",
            "duration-scale",
            EVENT_COLORS[index % len(EVENT_COLORS)],
            {"duration_minutes": str(duration)},
        )
        for index, duration in enumerate(durations)
    ]


def _overlap_columns(
    start: date, zone: ZoneInfo, max_group: int = 6, prefix: str = "overlap"
) -> list[CalendarEventDraft]:
    events: list[CalendarEventDraft] = []
    for size in range(1, max_group + 1):
        for position in range(1, size + 1):
            events.append(
                _event(
                    start,
                    8 + size,
                    0,
                    45,
                    zone,
                    f"{size}/{position}",
                    f"{prefix}-{size}",
                    EVENT_COLORS[(position - 1) % len(EVENT_COLORS)],
                    {"group_size": str(size), "group_position": str(position)},
                )
            )
    return events


def _color_palette(start: date, zone: ZoneInfo) -> list[CalendarEventDraft]:
    events: list[CalendarEventDraft] = []
    for index, color in enumerate(EVENT_COLORS):
        day = start + timedelta(days=index // 6)
        hour = 8 + index % 6
        events.append(
            _event(
                day,
                hour,
                0,
                40,
                zone,
                f"color {color[0]}",
                "color-palette",
                color,
                {
                    "color_id": color[0],
                    "logical_color_name": EVENT_COLOR_NAMES[color[0]],
                    "color_hex_approx": color[1],
                },
            )
        )
    return events


def _position_grid(start: date, zone: ZoneInfo) -> list[CalendarEventDraft]:
    positions = [
        (0, "monday", "M", 6, 0, "AM", 0),
        (0, "monday", "M", 12, 0, "MID", 12),
        (0, "monday", "M", 17, 30, "PM", 23),
        (2, "wednesday", "W", 6, 0, "AM", 0),
        (2, "wednesday", "W", 12, 0, "MID", 12),
        (2, "wednesday", "W", 17, 30, "PM", 23),
        (4, "friday", "F", 6, 0, "AM", 0),
        (4, "friday", "F", 12, 0, "MID", 12),
        (4, "friday", "F", 17, 30, "PM", 23),
    ]
    events: list[CalendarEventDraft] = []
    for index, (day_offset, logical_day, prefix, hour, minute, row_label, row) in enumerate(
        positions
    ):
        event_day = start + timedelta(days=day_offset)
        expected_start = datetime.combine(event_day, time(hour, minute), zone)
        events.append(
            _event(
                event_day,
                hour,
                minute,
                30,
                zone,
                f"{prefix}-{row_label}",
                "position-grid",
                EVENT_COLORS[index % len(EVENT_COLORS)],
                {
                    "day_offset": str(day_offset),
                    "logical_day": logical_day,
                    "logical_row": str(row),
                    "expected_start": expected_start.isoformat(),
                },
            )
        )
    return events


def _horizontal_bars(start: date, zone: ZoneInfo) -> list[CalendarEventDraft]:
    events: list[CalendarEventDraft] = []
    for units in range(1, 7):
        color = EVENT_COLORS[(units - 1) % len(EVENT_COLORS)]
        for position in range(1, units + 1):
            events.append(
                _event(
                    start,
                    8 + units,
                    0,
                    45,
                    zone,
                    f"B{units}/{position}",
                    f"bar-{units}",
                    color,
                    {
                        "logical_units": str(units),
                        "bar_width": str(units),
                        "group_position": str(position),
                        "cell_position": str(position),
                        "logical_start_column": "0",
                        "strategy": "independent-cells",
                    },
                )
            )
    return events


def _subcolumn_order(start: date, zone: ZoneInfo) -> list[CalendarEventDraft]:
    events: list[CalendarEventDraft] = []
    for row_index, (variant, slot_order) in enumerate(SUBCOLUMN_ORDER_VARIANTS):
        for creation_sequence, slot_index in enumerate(slot_order):
            events.append(
                _event(
                    start,
                    9 + row_index,
                    0,
                    45,
                    zone,
                    f"S{slot_index}",
                    f"slot-order-{variant}",
                    SUBCOLUMN_ORDER_COLORS[slot_index],
                    {
                        "row_index": str(row_index),
                        "subcolumn_index": str(slot_index),
                        "creation_sequence": str(creation_sequence),
                        "variant": variant,
                    },
                )
            )
    return events


def _vertical_compression(start: date, zone: ZoneInfo) -> list[CalendarEventDraft]:
    events: list[CalendarEventDraft] = []

    # CONTROL: three slots represented by four independent 30-minute cells.
    for segment in range(4):
        for slot in range(3):
            events.append(
                _event(
                    start,
                    6 + segment // 2,
                    30 * (segment % 2),
                    30,
                    zone,
                    f"{slot:02d}",
                    "vertical-control",
                    VERTICAL_COMPRESSION_COLOR,
                    {
                        "experiment": "control",
                        "representation": "unit-cells",
                        "subcolumn_index": str(slot),
                        "segment_index": str(segment),
                        "duration_minutes": "30",
                    },
                )
            )

    # COMPRESSED: the equivalent two-hour region as one event in each slot.
    for slot in range(6):
        events.append(
            _event(
                start,
                8,
                30,
                120,
                zone,
                f"{slot:02d}",
                "vertical-compressed",
                VERTICAL_COMPRESSION_COLOR,
                {
                    "experiment": "compressed",
                    "representation": "vertical-run",
                    "subcolumn_index": str(slot),
                    "duration_minutes": "120",
                },
            )
        )

    # MIXED LENGTH: fixed starts with different end times.
    for slot, duration in enumerate((30, 60, 90, 120, 90, 60)):
        events.append(
            _event(
                start,
                11,
                0,
                duration,
                zone,
                f"{slot:02d}",
                "vertical-mixed-length",
                VERTICAL_COMPRESSION_COLOR,
                {
                    "experiment": "mixed-length",
                    "representation": "vertical-run",
                    "subcolumn_index": str(slot),
                    "duration_minutes": str(duration),
                },
            )
        )

    # STAGGERED: partial overlaps approximate a compressed real frame.
    staggered = (
        (0, 14, 0, 120),
        (1, 14, 30, 60),
        (2, 14, 0, 60),
        (3, 15, 0, 60),
        (4, 14, 0, 150),
        (5, 14, 30, 90),
    )
    for slot, hour, minute, duration in staggered:
        events.append(
            _event(
                start,
                hour,
                minute,
                duration,
                zone,
                f"{slot:02d}",
                "vertical-staggered",
                VERTICAL_COMPRESSION_COLOR,
                {
                    "experiment": "staggered",
                    "representation": "vertical-run",
                    "subcolumn_index": str(slot),
                    "duration_minutes": str(duration),
                },
            )
        )
    return events


def _combined(start: date, zone: ZoneInfo) -> list[CalendarEventDraft]:
    events = [
        _event(
            start,
            8 + index,
            0,
            duration,
            zone,
            f"D{duration}",
            "combined-duration",
            EVENT_COLORS[index],
            {"duration_minutes": str(duration)},
        )
        for index, duration in enumerate([5, 15, 30, 60])
    ]
    events.extend(_overlap_columns(start + timedelta(days=1), zone, 5, "combined-overlap"))
    for index, color in enumerate(EVENT_COLORS[:4]):
        events.append(
            _event(
                start + timedelta(days=2),
                8 + index,
                0,
                40,
                zone,
                f"C{color[0]}",
                "combined-colors",
                color,
                {"color_id": color[0], "color_hex_approx": color[1]},
            )
        )
    for index, (day_offset, hour, label) in enumerate(
        [(3, 8, "P1"), (3, 15, "P2"), (5, 8, "P3"), (5, 15, "P4")]
    ):
        events.append(
            _event(
                start + timedelta(days=day_offset),
                hour,
                0,
                40,
                zone,
                label,
                "combined-positions",
                EVENT_COLORS[index],
            )
        )
    return events


def build_calibration_plan(
    pattern: CalibrationPattern,
    start_date: date,
    timezone: str = "America/Sao_Paulo",
    calendar_name: str = DEFAULT_CALENDAR_NAME,
    max_events: int = DEFAULT_MAX_EVENTS,
    run_id: str | None = None,
    now: datetime | None = None,
) -> CalibrationPlan:
    if not 1 <= max_events <= ABSOLUTE_MAX_EVENTS:
        raise CalendarAnimError(
            f"Calibration max-events must be between 1 and {ABSOLUTE_MAX_EVENTS}"
        )
    try:
        zone = ZoneInfo(timezone)
    except ZoneInfoNotFoundError as error:
        raise CalendarAnimError(f"Unknown timezone: {timezone}") from error
    builders = {
        "duration-scale": _duration_scale,
        "overlap-columns": _overlap_columns,
        "color-palette": _color_palette,
        "position-grid": _position_grid,
        "horizontal-bars": _horizontal_bars,
        "subcolumn-order": _subcolumn_order,
        "vertical-compression": _vertical_compression,
        "combined": _combined,
    }
    events = builders[pattern](start_date, zone)
    if len(events) > max_events:
        raise CalendarAnimError(
            f"Calibration requires {len(events)} events, but the configured limit is {max_events}. "
            "Increase --max-events explicitly to continue."
        )
    actual_run_id = run_id or generate_run_id(now)
    animation_id = f"calibration-{pattern}"
    finalized: list[CalendarEventDraft] = []
    for index, event in enumerate(events):
        metadata = {
            **event.private_metadata,
            "animation_id": animation_id,
            "run_id": actual_run_id,
            "pattern": pattern,
            "event_index": str(index),
            "generated_by": "calendar-anim",
        }
        finalized.append(event.model_copy(update={"private_metadata": metadata}))
    return CalibrationPlan(
        pattern=pattern,
        animation_id=animation_id,
        run_id=actual_run_id,
        calendar_name=calendar_name,
        start_date=start_date,
        timezone=timezone,
        max_events=max_events,
        events=finalized,
    )
