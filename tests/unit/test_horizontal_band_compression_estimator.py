from datetime import UTC, datetime, timedelta

import pytest

from calendar_anim.calendar.frame_mapping.models import CalendarMappedCell, CellRole
from calendar_anim.calendar.horizontal_band_compression.estimator import (
    estimate_synchronized_horizontal_bands,
)
from calendar_anim.exceptions import CalendarAnimError

pytestmark = pytest.mark.unit


def _cell(
    x: int,
    y: int,
    color_id: str = "2",
    role: CellRole = CellRole.FOREGROUND,
) -> CalendarMappedCell:
    start = datetime(2026, 1, 4, 6, tzinfo=UTC) + timedelta(minutes=30 * y)
    return CalendarMappedCell(
        logical_x=x,
        logical_y=y,
        day_offset=x // 6,
        subcolumn=x % 6,
        start=start,
        end=start + timedelta(minutes=30),
        color_id=color_id,
        color_hex="#33B679" if color_id == "2" else "#616161",
        cell_role=role,
    )


def test_equal_row_vectors_merge_into_synchronized_bands() -> None:
    cells: list[CalendarMappedCell] = []
    for y in range(4):
        color = "2" if y < 2 else "8"
        cells.extend(_cell(x, y, color) for x in range(6))

    estimate = estimate_synchronized_horizontal_bands(cells, 6, 4, 6, 1, 0)

    assert estimate.baseline_events == 24
    assert estimate.band_count == 2
    assert estimate.bands_per_day == [2]
    assert estimate.compressed_events == 12
    assert estimate.saved_events == 12
    assert estimate.reduction_percent == 50.0
    assert [band.length for band in estimate.bands] == [2, 2]
    assert all(len(band.slots) == 6 for band in estimate.bands)


def test_change_in_one_slot_splits_the_complete_horizontal_band() -> None:
    cells = [_cell(x, y) for y in range(3) for x in range(6)]
    cells = [
        _cell(cell.logical_x, cell.logical_y, "8")
        if (cell.logical_x, cell.logical_y) == (3, 1)
        else cell
        for cell in cells
    ]

    estimate = estimate_synchronized_horizontal_bands(cells, 6, 3, 6, 1, 0)

    assert estimate.band_count == 3
    assert estimate.compressed_events == 18
    assert estimate.saved_events == 0
    assert [band.length for band in estimate.bands] == [1, 1, 1]


def test_role_change_splits_band_even_when_color_is_equal() -> None:
    cells = [
        _cell(x, y, role=CellRole.BACKGROUND if y == 1 and x == 0 else CellRole.FOREGROUND)
        for y in range(2)
        for x in range(6)
    ]

    estimate = estimate_synchronized_horizontal_bands(cells, 6, 2, 6, 1, 0)

    assert estimate.band_count == 2
    assert estimate.foreground_events == 11
    assert estimate.background_events == 1


def test_day_boundaries_never_merge_even_with_identical_vectors() -> None:
    cells = [_cell(x, y) for y in range(4) for x in range(12)]

    estimate = estimate_synchronized_horizontal_bands(cells, 12, 4, 6, 2, 4)

    assert estimate.bands_per_day == [1, 1]
    assert estimate.band_count == 2
    assert estimate.compressed_events == 12
    assert estimate.longest_band_rows == 4
    assert estimate.average_band_length == 4.0


def test_uniform_42_by_24_grid_needs_one_six_event_band_per_day() -> None:
    cells = [_cell(x, y) for y in range(24) for x in range(42)]

    estimate = estimate_synchronized_horizontal_bands(cells, 42, 24, 6, 7, 0)

    assert estimate.baseline_events == 1008
    assert estimate.bands_per_day == [1] * 7
    assert estimate.band_count == 7
    assert estimate.compressed_events == 42
    assert estimate.saved_events == 966
    assert estimate.reduction_percent == 95.8


def test_invalid_dimensions_incomplete_and_duplicate_canvases_are_rejected() -> None:
    cells = [_cell(x, y) for y in range(2) for x in range(6)]
    with pytest.raises(CalendarAnimError, match="does not equal"):
        estimate_synchronized_horizontal_bands(cells, 6, 2, 5, 1, 0)

    with pytest.raises(CalendarAnimError, match="complete 6x2 canvas"):
        estimate_synchronized_horizontal_bands(cells[:-1], 6, 2, 6, 1, 0)

    duplicate = [*cells[:-1], cells[0]]
    with pytest.raises(CalendarAnimError, match="duplicate coordinates"):
        estimate_synchronized_horizontal_bands(duplicate, 6, 2, 6, 1, 0)
