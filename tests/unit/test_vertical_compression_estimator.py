from datetime import UTC, datetime, timedelta

import pytest

from calendar_anim.calendar.frame_mapping.models import CalendarMappedCell, CellRole
from calendar_anim.calendar.vertical_compression.estimator import estimate_vertical_runs
from calendar_anim.exceptions import CalendarAnimError

pytestmark = pytest.mark.unit


def _cell(
    x: int,
    y: int,
    color_id: str = "2",
    role: CellRole = CellRole.FOREGROUND,
) -> CalendarMappedCell:
    start = datetime(2026, 1, 5, 6, tzinfo=UTC) + timedelta(minutes=30 * y)
    return CalendarMappedCell(
        logical_x=x,
        logical_y=y,
        day_offset=0,
        subcolumn=x,
        start=start,
        end=start + timedelta(minutes=30),
        color_id=color_id,
        color_hex="#33B679" if color_id == "2" else "#616161",
        cell_role=role,
    )


def test_whole_column_with_same_color_and_role_becomes_one_run() -> None:
    estimate = estimate_vertical_runs([_cell(0, y) for y in range(4)], 1, 4, 0)

    assert estimate.baseline_events == 4
    assert estimate.compressed_runs == 1
    assert estimate.saved_events == 3
    assert estimate.reduction_percent == 75.0
    assert estimate.longest_vertical_run == 4
    assert estimate.average_run_length == 4.0


def test_alternating_colors_end_at_last_row_and_create_one_run_per_cell() -> None:
    cells = [_cell(0, y, "2" if y % 2 == 0 else "8") for y in range(4)]
    estimate = estimate_vertical_runs(cells, 1, 4, 3)

    assert estimate.compressed_runs == 4
    assert estimate.saved_events == 0
    assert estimate.runs[-1].start_y == 3
    assert estimate.runs[-1].length == 1


def test_role_change_splits_a_same_color_run_and_counts_roles() -> None:
    cells = [
        _cell(0, 0),
        _cell(0, 1),
        _cell(0, 2, role=CellRole.BACKGROUND),
        _cell(0, 3, role=CellRole.BACKGROUND),
    ]
    estimate = estimate_vertical_runs(cells, 1, 4, 0)

    assert estimate.compressed_runs == 2
    assert estimate.foreground_runs == 1
    assert estimate.background_runs == 1
    assert [run.length for run in estimate.runs] == [2, 2]


def test_multiple_columns_have_deterministic_metrics() -> None:
    cells = [_cell(0, y) for y in range(4)]
    cells.extend(_cell(1, y, "2" if y % 2 == 0 else "8", CellRole.BACKGROUND) for y in range(4))
    estimate = estimate_vertical_runs(cells, 2, 4, 7)

    assert estimate.frame_index == 7
    assert estimate.baseline_events == 8
    assert estimate.compressed_runs == 5
    assert estimate.saved_events == 3
    assert estimate.reduction_percent == 37.5
    assert estimate.foreground_runs == 1
    assert estimate.background_runs == 4
    assert estimate.average_run_length == 1.6


def test_full_42_by_24_uniform_grid_has_1008_cell_baseline() -> None:
    cells = [_cell(x, y) for x in range(42) for y in range(24)]
    estimate = estimate_vertical_runs(cells, 42, 24, 0)

    assert estimate.baseline_events == 1008
    assert estimate.compressed_runs == 42
    assert estimate.saved_events == 966
    assert estimate.reduction_percent == 95.8


def test_incomplete_or_duplicate_canvas_is_rejected() -> None:
    with pytest.raises(CalendarAnimError, match="complete 2x2 canvas"):
        estimate_vertical_runs([_cell(0, 0)], 2, 2, 0)

    duplicate = [_cell(0, 0), _cell(0, 0), _cell(1, 0), _cell(1, 1)]
    with pytest.raises(CalendarAnimError, match="duplicate coordinates"):
        estimate_vertical_runs(duplicate, 2, 2, 0)
