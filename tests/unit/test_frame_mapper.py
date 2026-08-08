from datetime import date, timedelta

import pytest

from calendar_anim.calendar.calibration.models import CalibrationProfile
from calendar_anim.calendar.frame_mapping.colors import map_calendar_color
from calendar_anim.calendar.frame_mapping.mapper import (
    build_single_frame_plan,
    expand_frame_blocks,
    fit_cells_contain,
    map_cells_to_calendar,
    resolve_week_start,
    select_frame,
)
from calendar_anim.calendar.frame_mapping.models import LogicalCell
from calendar_anim.exceptions import CalendarAnimError
from calendar_anim.models.frame import Block
from tests.factories import make_manifest, make_ready_calibration_profile

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("width", [1, 4])
def test_expand_block_creates_one_cell_per_unit(width: int) -> None:
    manifest = make_manifest(Block(x=0, y=1, width=width, color_id="0", color_hex="#112233"))
    manifest.render.grid_width = width
    cells = expand_frame_blocks(manifest.frames[0], width, 4)
    assert [(cell.x, cell.y) for cell in cells] == [(x, 1) for x in range(width)]
    assert {cell.color_hex for cell in cells} == {"#112233"}


def test_expand_rejects_out_of_bounds_and_overlapping_blocks() -> None:
    manifest = make_manifest(Block(x=3, y=0, width=2, color_id="0", color_hex="#112233"))
    with pytest.raises(CalendarAnimError, match="exceeds source grid"):
        expand_frame_blocks(manifest.frames[0], 4, 4)
    manifest.frames[0].blocks = [
        Block(x=0, y=0, width=2, color_id="0", color_hex="#112233"),
        Block(x=1, y=0, width=1, color_id="1", color_hex="#445566"),
    ]
    with pytest.raises(CalendarAnimError, match="overlapping blocks"):
        expand_frame_blocks(manifest.frames[0], 4, 4)


def test_contain_preserves_aspect_ratio_and_centers_cells() -> None:
    cells = [
        LogicalCell(
            x=x,
            y=y,
            source_x=x,
            source_y=y,
            color_hex="#112233",
            source_block_index=0,
        )
        for y in range(2)
        for x in range(2)
    ]
    fitted = fit_cells_contain(cells, 2, 2, 6, 4)
    assert len(fitted) == 16
    assert min(cell.x for cell in fitted) == 1
    assert max(cell.x for cell in fitted) == 4
    assert min(cell.y for cell in fitted) == 0
    assert max(cell.y for cell in fitted) == 3


def test_contain_keeps_equal_grid_coordinates() -> None:
    cell = LogicalCell(
        x=3,
        y=2,
        source_x=3,
        source_y=2,
        color_hex="#112233",
        source_block_index=0,
    )
    assert fit_cells_contain([cell], 4, 4, 4, 4) == [cell]


def test_sunday_week_start_is_resolved_from_any_anchor_day() -> None:
    assert resolve_week_start(date(2026, 9, 7), "sunday") == date(2026, 9, 6)


@pytest.mark.parametrize(
    ("x", "expected_day", "expected_subcolumn"),
    [(0, 0, 0), (5, 0, 5), (6, 1, 0), (41, 6, 5)],
)
def test_horizontal_mapping_uses_six_subcolumns_per_day(
    x: int, expected_day: int, expected_subcolumn: int
) -> None:
    cell = LogicalCell(
        x=x,
        y=0,
        source_x=x,
        source_y=0,
        color_hex="#039BE5",
        source_block_index=0,
    )
    mapped, _ = map_cells_to_calendar(
        [cell],
        make_ready_calibration_profile(),
        date(2026, 9, 6),
        "America/Sao_Paulo",
        "test-animation",
        "test-run",
        0,
        "#202124",
    )
    assert mapped[0].day_offset == expected_day
    assert mapped[0].subcolumn == expected_subcolumn
    assert mapped[0].start.date() == date(2026, 9, 6 + expected_day)


@pytest.mark.parametrize(
    ("y", "expected_hour", "expected_minute"),
    [(0, 6, 0), (1, 6, 30), (23, 17, 30)],
)
def test_vertical_mapping_uses_calibrated_row_duration(
    y: int, expected_hour: int, expected_minute: int
) -> None:
    cell = LogicalCell(
        x=0,
        y=y,
        source_x=0,
        source_y=y,
        color_hex="#039BE5",
        source_block_index=0,
    )
    mapped, _ = map_cells_to_calendar(
        [cell],
        make_ready_calibration_profile(),
        date(2026, 9, 6),
        "America/Sao_Paulo",
        "test-animation",
        "test-run",
        0,
        "#202124",
    )
    assert (mapped[0].start.hour, mapped[0].start.minute) == (
        expected_hour,
        expected_minute,
    )
    assert mapped[0].end - mapped[0].start == timedelta(minutes=30)
    assert mapped[0].start.tzinfo is not None


def test_color_mapper_handles_exact_nearest_contrast_and_determinism() -> None:
    assert map_calendar_color("#039BE5", ["1", "7"]).id == "7"
    assert map_calendar_color("#049AE4", ["1", "7"]).id == "7"
    first = map_calendar_color("#616161", ["7", "8"], "#616161", 3.0)
    second = map_calendar_color("#616161", ["7", "8"], "#616161", 3.0)
    assert first.id == second.id == "7"


def test_build_plan_expands_blocks_adds_metadata_and_statistics() -> None:
    manifest = make_manifest(Block(x=0, y=0, width=2, color_id="0", color_hex="#039BE5"))
    plan = build_single_frame_plan(
        manifest,
        make_ready_calibration_profile(),
        frame_index=0,
        anchor_date=date(2026, 9, 7),
        run_id="frame-test",
        max_execute_events=500,
    )
    assert plan.week_start_date == date(2026, 9, 6)
    assert plan.statistics.source_blocks == 1
    assert plan.statistics.expanded_logical_cells == 2
    assert plan.statistics.mapped_cells > 2
    assert plan.statistics.calendar_events == len(plan.events)
    assert plan.statistics.cells_per_event == 1
    assert plan.statistics.compression_ratio == 1
    assert plan.events[0].private_metadata["generated_by"] == "calendar-anim"
    assert plan.events[0].private_metadata["frame_index"] == "0"
    assert "logical_x" in plan.events[0].private_metadata


def test_frame_selection_reports_exact_valid_range() -> None:
    manifest = make_manifest()
    with pytest.raises(
        CalendarAnimError,
        match=r"Frame index 12 is out of range.*1 frames \(0-0\)",
    ):
        select_frame(manifest, 12)


def test_dry_plan_marks_incomplete_profile_without_blocking() -> None:
    profile = make_ready_calibration_profile()
    profile.horizontal_bar_mapping.independent_cells_appear_contiguous = None
    profile.horizontal_bar_mapping.recommended_horizontal_strategy = None
    profile = CalibrationProfile.model_validate(profile.model_dump())
    plan = build_single_frame_plan(
        make_manifest(),
        profile,
        frame_index=0,
        anchor_date=date(2026, 9, 6),
        run_id="incomplete",
        max_execute_events=1,
    )
    assert plan.profile_ready is False
    assert plan.horizontal_strategy == "unit-cells-only"
    assert any("NOT READY" in warning for warning in plan.warnings)
    assert any("execute limit" in warning for warning in plan.warnings)
