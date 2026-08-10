import json

import pytest

from calendar_anim.calendar.subcolumn_ordering import (
    EMPTY_EVENT_SUMMARY,
    SubcolumnOrderStrategy,
    format_summary_key,
    parse_subcolumn_order_strategy,
    serialize_summary_key,
    summary_for_subcolumn,
    summary_order_keys,
    zero_width_order_keys,
)
from calendar_anim.exceptions import CalendarAnimError

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("subcolumn", "expected"),
    [(0, "00"), (1, "01"), (5, "05")],
)
def test_summary_prefix_is_deterministic_and_zero_padded(subcolumn: int, expected: str) -> None:
    first = summary_for_subcolumn(subcolumn, 6, SubcolumnOrderStrategy.SUMMARY_PREFIX)
    second = summary_for_subcolumn(subcolumn, 6, "summary-prefix")

    assert first == second == expected


def test_explicit_numeric_is_deterministic_and_matches_legacy_summary_prefix() -> None:
    assert summary_order_keys(6, SubcolumnOrderStrategy.NUMERIC) == [
        "00",
        "01",
        "02",
        "03",
        "04",
        "05",
    ]
    assert summary_order_keys(6, SubcolumnOrderStrategy.NUMERIC) == summary_order_keys(
        6, SubcolumnOrderStrategy.SUMMARY_PREFIX
    )


@pytest.mark.parametrize("columns_per_day", [6, 18])
def test_zero_width_keys_are_unique_deterministic_and_lexicographic(
    columns_per_day: int,
) -> None:
    first = zero_width_order_keys(columns_per_day)
    second = zero_width_order_keys(columns_per_day)

    assert first == second
    assert len(first) == len(set(first)) == columns_per_day
    assert first == sorted(first)
    assert all(len(key) == 2 for key in first)


def test_zero_width_keys_preserve_validated_eighteen_slot_sequence() -> None:
    assert zero_width_order_keys(18) == [
        "\u200b\u200b",
        "\u200b\u200c",
        "\u200b\u200d",
        "\u200b\u2060",
        "\u200b\u2063",
        "\u200c\u200b",
        "\u200c\u200c",
        "\u200c\u200d",
        "\u200c\u2060",
        "\u200c\u2063",
        "\u200d\u200b",
        "\u200d\u200c",
        "\u200d\u200d",
        "\u200d\u2060",
        "\u200d\u2063",
        "\u2060\u200b",
        "\u2060\u200c",
        "\u2060\u200d",
    ]


def test_zero_width_generator_scales_using_only_validated_codepoints() -> None:
    keys = zero_width_order_keys(26)

    assert len(keys) == len(set(keys)) == 26
    assert keys == sorted(keys)
    assert {character for key in keys for character in key} <= {
        "\u200b",
        "\u200c",
        "\u200d",
        "\u2060",
        "\u2063",
    }
    assert {len(key) for key in keys} == {3}


def test_zero_width_keys_survive_json_round_trip_and_have_auditable_output() -> None:
    keys = summary_order_keys(18, SubcolumnOrderStrategy.ZERO_WIDTH)

    assert json.loads(json.dumps(keys)) == keys
    assert serialize_summary_key(keys[0]) == "U+200B U+200B"
    assert format_summary_key(keys[0]) == "U+200B U+200B"
    assert format_summary_key("00") == "'00' (U+0030 U+0030)"


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
