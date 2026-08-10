from enum import StrEnum
from itertools import product
from typing import Final

from calendar_anim.exceptions import CalendarAnimError


class SubcolumnOrderStrategy(StrEnum):
    """Mapper capabilities for ordering simultaneous Calendar events."""

    NONE = "none"
    CREATION_ORDER = "creation-order"
    NUMERIC = "numeric"
    ZERO_WIDTH = "zero-width"
    # Persisted plans created before invisible ordering use this exact value.
    # It remains a supported numeric strategy and must not be reinterpreted.
    SUMMARY_PREFIX = "summary-prefix"


SUPPORTED_SUBCOLUMN_ORDER_STRATEGIES: Final[frozenset[SubcolumnOrderStrategy]] = frozenset(
    SubcolumnOrderStrategy
)
EMPTY_EVENT_SUMMARY: Final = " "
DEFAULT_SUBCOLUMN_ORDER_STRATEGY: Final = SubcolumnOrderStrategy.ZERO_WIDTH
ZERO_WIDTH_ORDER_CODEPOINTS: Final[tuple[str, ...]] = (
    "\u200b",  # ZERO WIDTH SPACE
    "\u200c",  # ZERO WIDTH NON-JOINER
    "\u200d",  # ZERO WIDTH JOINER
    "\u2060",  # WORD JOINER
    "\u2063",  # INVISIBLE SEPARATOR
)


def parse_subcolumn_order_strategy(
    value: str | SubcolumnOrderStrategy,
) -> SubcolumnOrderStrategy:
    try:
        return SubcolumnOrderStrategy(value)
    except ValueError as error:
        supported = ", ".join(strategy.value for strategy in SubcolumnOrderStrategy)
        raise CalendarAnimError(
            f"Unsupported subcolumn order strategy {value!r}; supported: {supported}"
        ) from error


def summary_for_subcolumn(
    subcolumn: int,
    columns_per_day: int,
    strategy: str | SubcolumnOrderStrategy,
) -> str:
    """Return the event summary used as the horizontal slot-order key."""

    if columns_per_day <= 0:
        raise CalendarAnimError("columns per day must be positive")
    if not 0 <= subcolumn < columns_per_day:
        raise CalendarAnimError(
            f"Subcolumn {subcolumn} is outside the valid range 0-{columns_per_day - 1}"
        )

    resolved = parse_subcolumn_order_strategy(strategy)
    if resolved is SubcolumnOrderStrategy.ZERO_WIDTH:
        return zero_width_order_keys(columns_per_day)[subcolumn]
    if not is_numeric_order_strategy(resolved):
        return EMPTY_EVENT_SUMMARY

    width = max(2, len(str(columns_per_day - 1)))
    return f"{subcolumn:0{width}d}"


def summary_order_keys(
    columns_per_day: int,
    strategy: str | SubcolumnOrderStrategy,
) -> list[str]:
    resolved = parse_subcolumn_order_strategy(strategy)
    if not uses_summary_ordering(resolved):
        return []
    return [
        summary_for_subcolumn(subcolumn, columns_per_day, resolved)
        for subcolumn in range(columns_per_day)
    ]


def zero_width_order_keys(columns_per_day: int) -> list[str]:
    """Return stable, unique, lexicographically ordered invisible keys.

    Two-codepoint combinations are retained for up to 25 slots so the validated
    18-slot sequence remains unchanged. Larger grids increase the combination
    length while using only the same five calibrated Unicode codepoints.
    """

    if columns_per_day <= 0:
        raise CalendarAnimError("columns per day must be positive")
    key_length = 2
    capacity = len(ZERO_WIDTH_ORDER_CODEPOINTS) ** key_length
    while capacity < columns_per_day:
        key_length += 1
        capacity *= len(ZERO_WIDTH_ORDER_CODEPOINTS)
    candidates = (
        "".join(characters)
        for characters in product(ZERO_WIDTH_ORDER_CODEPOINTS, repeat=key_length)
    )
    return [next(candidates) for _ in range(columns_per_day)]


def is_numeric_order_strategy(strategy: SubcolumnOrderStrategy) -> bool:
    """Return whether a strategy uses visible numeric summary keys."""

    return strategy in {
        SubcolumnOrderStrategy.NUMERIC,
        SubcolumnOrderStrategy.SUMMARY_PREFIX,
    }


def uses_summary_ordering(strategy: SubcolumnOrderStrategy) -> bool:
    """Return whether Calendar summary values are the ordering key."""

    return strategy is SubcolumnOrderStrategy.ZERO_WIDTH or is_numeric_order_strategy(strategy)


def serialize_summary_key(value: str) -> str:
    """Represent a summary key visibly without changing the submitted value."""

    return " ".join(f"U+{ord(character):04X}" for character in value)


def format_summary_key(value: str) -> str:
    """Return a human-auditable report representation for a summary key."""

    codepoints = serialize_summary_key(value)
    if value.isprintable() and not value.isspace():
        return f"{value!r} ({codepoints})"
    return codepoints
