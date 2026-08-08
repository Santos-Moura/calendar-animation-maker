from enum import StrEnum
from typing import Final

from calendar_anim.exceptions import CalendarAnimError


class SubcolumnOrderStrategy(StrEnum):
    """Mapper capabilities for ordering simultaneous Calendar events."""

    NONE = "none"
    CREATION_ORDER = "creation-order"
    SUMMARY_PREFIX = "summary-prefix"


SUPPORTED_SUBCOLUMN_ORDER_STRATEGIES: Final[frozenset[SubcolumnOrderStrategy]] = frozenset(
    SubcolumnOrderStrategy
)
EMPTY_EVENT_SUMMARY: Final = " "


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
    if resolved is not SubcolumnOrderStrategy.SUMMARY_PREFIX:
        return EMPTY_EVENT_SUMMARY

    width = max(2, len(str(columns_per_day - 1)))
    return f"{subcolumn:0{width}d}"


def summary_order_keys(
    columns_per_day: int,
    strategy: str | SubcolumnOrderStrategy,
) -> list[str]:
    resolved = parse_subcolumn_order_strategy(strategy)
    if resolved is not SubcolumnOrderStrategy.SUMMARY_PREFIX:
        return []
    return [
        summary_for_subcolumn(subcolumn, columns_per_day, resolved)
        for subcolumn in range(columns_per_day)
    ]
