import hashlib
from datetime import date, timedelta

import pytest

from calendar_anim.calendar.calibration.models import CalibrationProfile
from calendar_anim.calendar.frame_mapping.colors import (
    calendar_palette_color,
    map_calendar_color,
)
from calendar_anim.calendar.frame_mapping.mapper import (
    build_full_grid_cells,
    build_single_frame_plan,
    expand_frame_blocks,
    fit_cells_contain,
    map_cells_to_calendar,
    resolve_week_start,
    select_frame,
)
from calendar_anim.calendar.frame_mapping.models import (
    CellRole,
    EventCompressionMode,
    FrameMappingMode,
    LogicalCell,
)
from calendar_anim.calendar.subcolumn_ordering import (
    SubcolumnOrderStrategy,
    summary_order_keys,
)
from calendar_anim.exceptions import CalendarAnimError
from calendar_anim.models.frame import Block
from tests.factories import make_manifest, make_ready_calibration_profile

pytestmark = pytest.mark.unit


def _small_profile(height: int = 1) -> CalibrationProfile:
    data = make_ready_calibration_profile().model_dump()
    data["calendar_ui"]["visible_start_hour"] = 6
    data["calendar_ui"]["visible_end_hour"] = 6 + height
    data["vertical_mapping"]["minimum_distinguishable_height_minutes"] = 60
    data["horizontal_mapping"]["days_used"] = 1
    return CalibrationProfile.model_validate(data)


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


def test_expand_ignores_legacy_blocks_matching_configured_background() -> None:
    manifest = make_manifest(Block(x=0, y=0, width=1, color_id="0", color_hex="#000000"))
    manifest.frames[0].blocks.append(Block(x=1, y=0, width=1, color_id="1", color_hex="#555555"))

    cells = expand_frame_blocks(manifest.frames[0], 2, 1, "#000000")

    assert [(cell.x, cell.color_hex) for cell in cells] == [(1, "#555555")]


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


def test_cayde_final_palette_is_locked_and_deterministic() -> None:
    manifest = make_manifest(Block(x=0, y=0, width=1, color_id="1", color_hex="#7986CB"))
    manifest.frames[0].blocks.extend(
        [
            Block(x=1, y=0, width=1, color_id="3", color_hex="#8E24AA"),
            Block(x=2, y=0, width=1, color_id="4", color_hex="#E67C73"),
        ]
    )
    arguments = {
        "manifest": manifest,
        "profile": make_ready_calibration_profile(),
        "frame_index": 0,
        "anchor_date": date(2026, 9, 6),
        "run_id": "cayde-palette-regression",
        "max_execute_events": 2000,
        "mapping_mode": FrameMappingMode.FULL_GRID,
        "event_compression": EventCompressionMode.SYNCHRONIZED_HORIZONTAL_BANDS,
        "palette_preset": "cayde-final",
    }

    first = build_single_frame_plan(**arguments)
    second = build_single_frame_plan(**arguments)

    assert first == second
    assert first.palette_preset == "cayde-final"
    assert first.background_color_id == "1"
    assert first.foreground_color_ids == ["1", "2", "3", "4"]
    foreground = [
        cell.color_id for cell in first.mapped_cells if cell.cell_role is CellRole.FOREGROUND
    ]
    assert set(foreground) == {"1", "3", "4"}
    signature = hashlib.sha256(
        first.model_dump_json(exclude={"warnings"}).encode("utf-8")
    ).hexdigest()
    assert signature == "d5c733504066e118d013264a8586a4d26501457378f32012e905b3658c9b9644"


def test_candidate_palette_remaps_source_canvas_without_changing_final_preset() -> None:
    manifest = make_manifest(Block(x=0, y=0, width=1, color_id="1", color_hex="#7986CB"))
    manifest.frames[0].blocks.append(Block(x=1, y=0, width=1, color_id="3", color_hex="#8E24AA"))

    candidate = build_single_frame_plan(
        manifest,
        make_ready_calibration_profile(),
        frame_index=0,
        anchor_date=date(2026, 9, 6),
        run_id="cayde-candidate-background",
        max_execute_events=2000,
        mapping_mode=FrameMappingMode.FULL_GRID,
        event_compression=EventCompressionMode.SYNCHRONIZED_HORIZONTAL_BANDS,
        palette_preset="cayde-lilac-pop",
    )

    canvas = next(cell for cell in candidate.mapped_cells if cell.source_block_index == 0)
    character = next(cell for cell in candidate.mapped_cells if cell.source_block_index == 1)
    assert canvas.cell_role is CellRole.BACKGROUND
    assert canvas.color_id == "1"
    assert character.cell_role is CellRole.FOREGROUND
    assert character.color_id == "3"


def test_calendar_background_color_is_explicit_deterministic_and_validated() -> None:
    assert calendar_palette_color().id == "8"
    assert calendar_palette_color("5").hex == "#F6BF26"
    with pytest.raises(CalendarAnimError, match="Unsupported Calendar background color ID"):
        calendar_palette_color("99")


def test_full_grid_six_by_one_fills_every_cell_and_marks_roles() -> None:
    foreground = [
        LogicalCell(
            x=3,
            y=0,
            source_x=3,
            source_y=0,
            color_hex="#039BE5",
            source_block_index=0,
        )
    ]
    cells = build_full_grid_cells(foreground, 6, 1, "#616161")
    assert len(cells) == 6
    assert [cell.cell_role for cell in cells] == [
        CellRole.BACKGROUND,
        CellRole.BACKGROUND,
        CellRole.BACKGROUND,
        CellRole.FOREGROUND,
        CellRole.BACKGROUND,
        CellRole.BACKGROUND,
    ]
    assert {cell.color_hex for cell in cells if cell.cell_role is CellRole.BACKGROUND} == {
        "#616161"
    }


def test_full_grid_plan_has_fillers_metadata_and_exact_canvas_size() -> None:
    manifest = make_manifest(Block(x=3, y=0, width=1, color_id="0", color_hex="#039BE5"))
    manifest.render.grid_width = 6
    manifest.render.grid_height = 1
    plan = build_single_frame_plan(
        manifest,
        _small_profile(),
        frame_index=0,
        anchor_date=date(2026, 9, 6),
        run_id="full-grid",
        max_execute_events=1200,
        mapping_mode=FrameMappingMode.FULL_GRID,
        calendar_background_color_id="8",
    )
    assert plan.event_count == plan.target_grid_width * plan.target_grid_height == 6
    assert plan.statistics.foreground_events == 1
    assert plan.statistics.background_events == 5
    assert plan.statistics.total_logical_cells == 6
    assert plan.statistics.sparse_event_estimate == 1
    assert plan.statistics.full_grid_event_estimate == 6
    assert plan.background_color_id == "8"
    assert [event.private_metadata["subcolumn_index"] for event in plan.events] == [
        "0",
        "1",
        "2",
        "3",
        "4",
        "5",
    ]
    assert plan.events[0].private_metadata["cell_role"] == "background"
    assert plan.events[3].private_metadata["cell_role"] == "foreground"
    assert plan.events[0].color_id == "8"
    expected_summaries = summary_order_keys(6, SubcolumnOrderStrategy.ZERO_WIDTH)
    assert [event.summary for event in plan.events] == expected_summaries
    assert plan.events[3].summary == "\u200b\u2060"
    assert plan.events[3].private_metadata["day_offset"] == "0"
    assert plan.events[3].private_metadata["subcolumn_order_strategy"] == "zero-width"
    assert plan.events[3].private_metadata["subcolumn_order_key"] == "\u200b\u2060"
    assert plan.events[3].private_metadata["subcolumn_order_key_codepoints"] == ("U+200B U+2060")


def test_synchronized_band_compression_keeps_canvas_and_merges_equal_row_vectors() -> None:
    manifest = make_manifest(Block(x=0, y=0, width=6, height=2, color_id="0", color_hex="#039BE5"))
    manifest.render.grid_width = 6
    manifest.render.grid_height = 4
    plan = build_single_frame_plan(
        manifest,
        _small_profile(height=4),
        frame_index=0,
        anchor_date=date(2026, 9, 6),
        run_id="compressed-bands",
        max_execute_events=1200,
        mapping_mode=FrameMappingMode.FULL_GRID,
        event_compression=EventCompressionMode.SYNCHRONIZED_HORIZONTAL_BANDS,
        calendar_background_color_id="8",
    )

    assert len(plan.mapped_cells) == 24
    assert plan.event_count == 12
    assert plan.statistics.baseline_calendar_events == 24
    assert plan.statistics.saved_calendar_events == 12
    assert plan.statistics.synchronized_horizontal_bands == 2
    assert plan.statistics.foreground_events == 6
    assert plan.statistics.background_events == 6
    assert plan.statistics.cells_per_event == 2
    assert plan.statistics.compression_ratio == 0.5
    assert {event.end - event.start for event in plan.events} == {timedelta(hours=2)}
    assert [event.summary for event in plan.events[:6]] == summary_order_keys(
        6, SubcolumnOrderStrategy.ZERO_WIDTH
    )
    assert plan.events[0].private_metadata["band_start_y"] == "0"
    assert plan.events[0].private_metadata["band_end_y_exclusive"] == "2"
    assert plan.events[0].private_metadata["band_length_rows"] == "2"
    assert plan.events[0].private_metadata["event_compression"] == "synchronized-horizontal-bands"
    assert plan.profile_ready is True


def test_synchronized_band_compression_requires_full_grid_and_approved_profile() -> None:
    with pytest.raises(CalendarAnimError, match="requires full-grid"):
        build_single_frame_plan(
            make_manifest(),
            make_ready_calibration_profile(),
            frame_index=0,
            anchor_date=date(2026, 9, 6),
            run_id="invalid-compression",
            max_execute_events=1200,
            mapping_mode=FrameMappingMode.SPARSE,
            event_compression=EventCompressionMode.SYNCHRONIZED_HORIZONTAL_BANDS,
        )

    profile = make_ready_calibration_profile()
    profile.synchronized_horizontal_bands = None
    plan = build_single_frame_plan(
        make_manifest(),
        profile,
        frame_index=0,
        anchor_date=date(2026, 9, 6),
        run_id="unapproved-compression",
        max_execute_events=1200,
        mapping_mode=FrameMappingMode.FULL_GRID,
        event_compression=EventCompressionMode.SYNCHRONIZED_HORIZONTAL_BANDS,
    )

    assert plan.profile_ready is False
    assert any("not approved" in warning for warning in plan.warnings)


def test_full_grid_contain_centers_foreground_and_fills_borders() -> None:
    manifest = make_manifest(Block(x=0, y=0, width=2, color_id="0", color_hex="#039BE5"))
    manifest.render.grid_width = 2
    manifest.render.grid_height = 2
    manifest.frames[0].blocks.append(Block(x=0, y=1, width=2, color_id="0", color_hex="#039BE5"))
    profile = _small_profile(height=4)
    plan = build_single_frame_plan(
        manifest,
        profile,
        frame_index=0,
        anchor_date=date(2026, 9, 6),
        run_id="contained",
        max_execute_events=1200,
        mapping_mode=FrameMappingMode.FULL_GRID,
    )
    foreground_x = {
        cell.logical_x for cell in plan.mapped_cells if cell.cell_role is CellRole.FOREGROUND
    }
    assert foreground_x == {1, 2, 3, 4}
    assert all(
        cell.cell_role is CellRole.BACKGROUND
        for cell in plan.mapped_cells
        if cell.logical_x in {0, 5}
    )


def test_full_grid_with_full_foreground_needs_no_fillers() -> None:
    manifest = make_manifest(Block(x=0, y=0, width=6, color_id="0", color_hex="#039BE5"))
    manifest.render.grid_width = 6
    manifest.render.grid_height = 1
    plan = build_single_frame_plan(
        manifest,
        _small_profile(),
        frame_index=0,
        anchor_date=date(2026, 9, 6),
        run_id="all-foreground",
        max_execute_events=1200,
        mapping_mode=FrameMappingMode.FULL_GRID,
    )
    assert plan.statistics.foreground_events == 6
    assert plan.statistics.background_events == 0


def test_sparse_mode_preserves_foreground_only_count() -> None:
    plan = build_single_frame_plan(
        make_manifest(),
        make_ready_calibration_profile(),
        frame_index=0,
        anchor_date=date(2026, 9, 6),
        run_id="sparse",
        max_execute_events=500,
        mapping_mode=FrameMappingMode.SPARSE,
        event_compression=EventCompressionMode.NONE,
    )
    assert plan.background_color_id is None
    assert plan.statistics.background_events == 0
    assert plan.event_count == plan.statistics.foreground_events
    assert all(event.private_metadata["cell_role"] == "foreground" for event in plan.events)
    assert plan.subcolumn_order_strategy is SubcolumnOrderStrategy.NONE
    assert {event.summary for event in plan.events} == {" "}


def test_full_grid_order_and_six_columns_are_deterministic_for_every_day_row() -> None:
    plan = build_single_frame_plan(
        make_manifest(),
        make_ready_calibration_profile(),
        frame_index=0,
        anchor_date=date(2026, 9, 6),
        run_id="ordered",
        max_execute_events=1200,
        mapping_mode=FrameMappingMode.FULL_GRID,
        event_compression=EventCompressionMode.NONE,
    )
    order = [
        (
            cell.day_offset,
            cell.logical_y,
            cell.subcolumn,
        )
        for cell in plan.mapped_cells
    ]
    assert order == sorted(order)
    assert order[-1] == (6, 23, 5)
    event_order = [
        (
            int(event.private_metadata["logical_x"]) // plan.columns_per_day,
            int(event.private_metadata["logical_y"]),
            int(event.private_metadata["subcolumn_index"]),
        )
        for event in plan.events
    ]
    assert event_order == order
    assert plan.subcolumn_order_strategy is SubcolumnOrderStrategy.ZERO_WIDTH
    assert plan.subcolumn_order_keys == summary_order_keys(6, SubcolumnOrderStrategy.ZERO_WIDTH)
    groups: dict[tuple[int, int], list[int]] = {}
    for cell in plan.mapped_cells:
        groups.setdefault((cell.day_offset, cell.logical_y), []).append(cell.subcolumn)
    assert len(groups) == 7 * 24
    assert all(subcolumns == list(range(6)) for subcolumns in groups.values())
    for day_row, subcolumns in groups.items():
        cells = [cell for cell in plan.mapped_cells if (cell.day_offset, cell.logical_y) == day_row]
        assert len(subcolumns) == 6
        assert len({(cell.start, cell.end) for cell in cells}) == 1
    event_groups: dict[tuple[int, int], list[str]] = {}
    for event in plan.events:
        key = (
            int(event.private_metadata["day_offset"]),
            int(event.private_metadata["logical_y"]),
        )
        event_groups.setdefault(key, []).append(event.summary)
    expected_summaries = summary_order_keys(6, SubcolumnOrderStrategy.ZERO_WIDTH)
    assert all(summaries == expected_summaries for summaries in event_groups.values())


def test_full_grid_summary_depends_only_on_subcolumn() -> None:
    manifest = make_manifest(Block(x=2, y=0, width=1, color_id="0", color_hex="#039BE5"))
    manifest.render.grid_width = 6
    manifest.render.grid_height = 1
    plan = build_single_frame_plan(
        manifest,
        _small_profile(height=2),
        frame_index=0,
        anchor_date=date(2026, 9, 6),
        run_id="summary-independent",
        max_execute_events=1200,
        mapping_mode=FrameMappingMode.FULL_GRID,
    )

    first_row = plan.events[:6]
    second_row = plan.events[6:12]
    expected_summaries = summary_order_keys(6, SubcolumnOrderStrategy.ZERO_WIDTH)
    assert [event.summary for event in first_row] == expected_summaries
    assert [event.summary for event in second_row] == expected_summaries
    assert first_row[2].private_metadata["cell_role"] == "foreground"
    assert second_row[2].private_metadata["cell_role"] == "background"
    assert first_row[2].color_id != second_row[2].color_id
    assert first_row[2].summary == second_row[2].summary == "\u200b\u200d"


def test_numeric_fallback_and_legacy_plan_remain_numeric() -> None:
    kwargs = {
        "manifest": make_manifest(),
        "profile": make_ready_calibration_profile(),
        "frame_index": 0,
        "anchor_date": date(2026, 9, 6),
        "max_execute_events": 1200,
        "mapping_mode": FrameMappingMode.FULL_GRID,
        "event_compression": EventCompressionMode.SYNCHRONIZED_HORIZONTAL_BANDS,
    }
    numeric = build_single_frame_plan(
        **kwargs,
        run_id="numeric-fallback",
        subcolumn_order_strategy=SubcolumnOrderStrategy.NUMERIC,
    )
    legacy = build_single_frame_plan(
        **kwargs,
        run_id="legacy-summary-prefix",
        subcolumn_order_strategy=SubcolumnOrderStrategy.SUMMARY_PREFIX,
    )

    expected = ["00", "01", "02", "03", "04", "05"]
    assert numeric.subcolumn_order_strategy is SubcolumnOrderStrategy.NUMERIC
    assert legacy.subcolumn_order_strategy is SubcolumnOrderStrategy.SUMMARY_PREFIX
    assert numeric.subcolumn_order_keys == legacy.subcolumn_order_keys == expected
    assert [event.summary for event in numeric.events[:6]] == expected
    assert [event.summary for event in legacy.events[:6]] == expected
    restored = type(legacy).model_validate_json(legacy.model_dump_json())
    assert restored.subcolumn_order_strategy is SubcolumnOrderStrategy.SUMMARY_PREFIX
    assert [event.summary for event in restored.events] == [
        event.summary for event in legacy.events
    ]


def test_numeric_and_zero_width_change_only_summary_ordering_fields() -> None:
    kwargs = {
        "manifest": make_manifest(),
        "profile": make_ready_calibration_profile(),
        "frame_index": 0,
        "anchor_date": date(2026, 9, 6),
        "max_execute_events": 1200,
        "mapping_mode": FrameMappingMode.FULL_GRID,
        "event_compression": EventCompressionMode.SYNCHRONIZED_HORIZONTAL_BANDS,
    }
    numeric = build_single_frame_plan(
        **kwargs,
        run_id="same-plan",
        subcolumn_order_strategy=SubcolumnOrderStrategy.NUMERIC,
    )
    invisible = build_single_frame_plan(
        **kwargs,
        run_id="same-plan",
        subcolumn_order_strategy=SubcolumnOrderStrategy.ZERO_WIDTH,
    )

    assert numeric.mapped_cells == invisible.mapped_cells
    assert numeric.statistics == invisible.statistics
    assert numeric.event_count == invisible.event_count
    for numeric_event, invisible_event in zip(numeric.events, invisible.events, strict=True):
        assert numeric_event.start == invisible_event.start
        assert numeric_event.end == invisible_event.end
        assert numeric_event.color_id == invisible_event.color_id
        assert numeric_event.summary != invisible_event.summary
        numeric_metadata = dict(numeric_event.private_metadata)
        invisible_metadata = dict(invisible_event.private_metadata)
        for key in (
            "subcolumn_order_strategy",
            "subcolumn_order_key",
            "subcolumn_order_key_codepoints",
        ):
            numeric_metadata.pop(key)
            invisible_metadata.pop(key)
        assert numeric_metadata == invisible_metadata


def test_explicit_none_strategy_preserves_blank_full_grid_summaries() -> None:
    plan = build_single_frame_plan(
        make_manifest(),
        make_ready_calibration_profile(),
        frame_index=0,
        anchor_date=date(2026, 9, 6),
        run_id="full-grid-none",
        max_execute_events=1200,
        mapping_mode=FrameMappingMode.FULL_GRID,
        subcolumn_order_strategy=SubcolumnOrderStrategy.NONE,
    )

    assert plan.subcolumn_order_strategy is SubcolumnOrderStrategy.NONE
    assert plan.subcolumn_order_keys == []
    assert {event.summary for event in plan.events} == {" "}
    assert plan.profile_ready is False
    assert any("does not confirm that strategy" in warning for warning in plan.warnings)


def test_background_color_is_not_remapped_by_foreground_contrast() -> None:
    manifest = make_manifest(Block(x=0, y=0, width=1, color_id="0", color_hex="#616161"))
    manifest.render.grid_width = 6
    manifest.render.grid_height = 1
    plan = build_single_frame_plan(
        manifest,
        _small_profile(),
        frame_index=0,
        anchor_date=date(2026, 9, 6),
        run_id="background-color",
        max_execute_events=1200,
        mapping_mode=FrameMappingMode.FULL_GRID,
        calendar_background_color_id="8",
    )
    background = [cell for cell in plan.mapped_cells if cell.cell_role is CellRole.BACKGROUND]
    assert background
    assert {cell.color_id for cell in background} == {"8"}


def test_build_plan_reports_and_ignores_legacy_background_cells() -> None:
    manifest = make_manifest(Block(x=0, y=0, width=1, color_id="0", color_hex="#000000"))
    manifest.render.grid_width = 2
    manifest.render.grid_height = 1
    manifest.render.background = "#000000"
    manifest.frames[0].blocks.append(Block(x=1, y=0, width=1, color_id="1", color_hex="#555555"))

    plan = build_single_frame_plan(
        manifest,
        _small_profile(),
        frame_index=0,
        anchor_date=date(2026, 9, 6),
        run_id="legacy-background",
        max_execute_events=1200,
        mapping_mode=FrameMappingMode.FULL_GRID,
    )

    assert plan.statistics.source_blocks == 2
    assert plan.statistics.expanded_logical_cells == 1
    assert plan.statistics.foreground_events == 1
    assert any("Ignored 1 legacy manifest cell" in warning for warning in plan.warnings)


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
    assert plan.mapping_mode is FrameMappingMode.FULL_GRID
    assert plan.event_compression is EventCompressionMode.SYNCHRONIZED_HORIZONTAL_BANDS
    assert plan.statistics.mapped_cells == 42 * 24
    assert plan.statistics.calendar_events == len(plan.events)
    assert plan.statistics.calendar_events < plan.statistics.baseline_calendar_events
    assert plan.statistics.cells_per_event > 1
    assert plan.statistics.compression_ratio < 1
    assert plan.events[0].private_metadata["generated_by"] == "calendar-anim"
    assert plan.events[0].private_metadata["frame_index"] == "0"
    assert plan.events[0].private_metadata["event_compression"] == ("synchronized-horizontal-bands")


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
    assert "horizontal-bars calibration" in profile.missing_mapper_calibrations
    assert any("NOT READY" in warning for warning in plan.warnings)
    assert any("execute limit" in warning for warning in plan.warnings)
