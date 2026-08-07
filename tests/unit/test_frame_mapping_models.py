from datetime import UTC, date, datetime, timedelta

import pytest

from calendar_anim.calendar.frame_mapping.models import (
    CalendarMappedCell,
    CellRole,
    FrameMappingMode,
    FrameMappingStatistics,
    SingleFrameCalendarPlan,
)
from calendar_anim.calendar.models import CalendarEventDraft

pytestmark = pytest.mark.unit


def test_single_frame_plan_serializes_mapping_metadata() -> None:
    start = datetime(2026, 9, 6, 6, tzinfo=UTC)
    mapped = CalendarMappedCell(
        logical_x=0,
        logical_y=0,
        source_x=0,
        source_y=0,
        day_offset=0,
        subcolumn=0,
        start=start,
        end=start + timedelta(minutes=30),
        color_id="7",
        color_hex="#039BE5",
        source_block_index=0,
    )
    event = CalendarEventDraft(
        frame_index=0,
        block_index=0,
        start=mapped.start,
        end=mapped.end,
        color_id=mapped.color_id,
        summary="calendar-anim:test-animation:frame-0",
        private_metadata={"logical_x": "0", "logical_y": "0"},
    )
    plan = SingleFrameCalendarPlan(
        animation_id="test-animation",
        run_id="frame-test",
        frame_index=0,
        timezone="UTC",
        week_start_date=date(2026, 9, 6),
        source_grid_width=1,
        source_grid_height=1,
        target_grid_width=1,
        target_grid_height=1,
        profile_ready=False,
        horizontal_strategy="unit-cells-only",
        max_execute_events=500,
        statistics=FrameMappingStatistics(
            source_blocks=1,
            expanded_logical_cells=1,
            non_background_cells=1,
            mapped_cells=1,
            calendar_events=1,
            unique_calendar_colors=1,
            cells_per_event=1,
            compression_ratio=1,
        ),
        mapped_cells=[mapped],
        events=[event],
    )
    assert plan.event_count == 1
    assert plan.mapping_mode is FrameMappingMode.SPARSE
    assert plan.background_color_id is None
    assert '"frame_index":0' in plan.model_dump_json()


def test_full_grid_fields_serialize_without_breaking_sparse_defaults() -> None:
    plan_data = {
        "animation_id": "test-animation",
        "run_id": "full-grid-test",
        "frame_index": 0,
        "timezone": "UTC",
        "week_start_date": "2026-09-06",
        "source_grid_width": 6,
        "source_grid_height": 1,
        "target_grid_width": 6,
        "target_grid_height": 1,
        "mapping_mode": "full-grid",
        "background_color_id": "8",
        "profile_ready": True,
        "horizontal_strategy": "independent-cells",
        "max_execute_events": 1200,
        "statistics": {
            "source_blocks": 1,
            "expanded_logical_cells": 1,
            "non_background_cells": 1,
            "mapped_cells": 6,
            "calendar_events": 6,
            "unique_calendar_colors": 2,
            "cells_per_event": 1,
            "compression_ratio": 1,
            "foreground_cells_after_fitting": 1,
            "background_structural_cells": 5,
            "total_logical_cells": 6,
            "foreground_events": 1,
            "background_events": 5,
            "foreground_calendar_colors": 1,
            "sparse_event_estimate": 1,
            "full_grid_event_estimate": 6,
        },
        "mapped_cells": [],
        "events": [],
    }
    plan = SingleFrameCalendarPlan.model_validate(plan_data)
    assert plan.mapping_mode is FrameMappingMode.FULL_GRID
    assert plan.statistics.background_structural_cells == 5

    legacy_cell = CalendarMappedCell.model_validate(
        {
            "logical_x": 0,
            "logical_y": 0,
            "source_x": 0,
            "source_y": 0,
            "day_offset": 0,
            "subcolumn": 0,
            "start": "2026-09-06T06:00:00+00:00",
            "end": "2026-09-06T06:30:00+00:00",
            "color_id": "7",
            "color_hex": "#039BE5",
            "source_block_index": 0,
        }
    )
    assert legacy_cell.cell_role is CellRole.FOREGROUND
