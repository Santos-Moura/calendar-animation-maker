import pytest

from calendar_anim.calendar.subcolumn_ordering import (
    EMPTY_EVENT_SUMMARY,
    SubcolumnOrderStrategy,
    parse_subcolumn_order_strategy,
    summary_for_subcolumn,
    summary_order_keys,
)
from calendar_anim.exceptions import CalendarAnimError

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("subcolumn", "expected"),
    [(0, "00"), (1, "01"), (5, "05")],
)
def test_summary_prefix_is_deterministic_and_zero_padded(
    subcolumn: int, expected: str
) -> None:
    first = summary_for_subcolumn(subcolumn, 6, SubcolumnOrderStrategy.SUMMARY_PREFIX)
    second = summary_for_subcolumn(subcolumn, 6, "summary-prefix")

    assert first == second == expected


@pytest.mark.parametrize("subcolumn", [-1, 6])
def test_summary_prefix_rejects_invalid_subcolumns(subcolumn: int) -> None:
    with pytest.raises(CalendarAnimError, match="outside the valid range"):
        summary_for_subcolumn(subcolumn, 6, SubcolumnOrderStrategy.SUMMARY_PREFIX)


@pytest.mark.parametrize(
    "strategy",
    [SubcolumnOrderStrategy.NONE, SubcolumnOrderStrategy.CREATION_ORDER],
)
def test_non_summary_strategies_preserve_blank_event_title(
    strategy: SubcolumnOrderStrategy,
) -> None:
    assert summary_for_subcolumn(3, 6, strategy) == EMPTY_EVENT_SUMMARY
    assert summary_order_keys(6, strategy) == []


def test_summary_order_keys_cover_all_six_slots() -> None:
    assert summary_order_keys(6, "summary-prefix") == [
        "00",
        "01",
        "02",
        "03",
        "04",
        "05",
    ]


def test_invalid_strategy_fails_with_supported_values() -> None:
    with pytest.raises(CalendarAnimError, match="Unsupported subcolumn order strategy"):
        parse_subcolumn_order_strategy("color-order")
