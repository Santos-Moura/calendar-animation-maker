from datetime import date, timedelta
from pathlib import Path

import pytest

from calendar_anim.calendar.frame_mapping.models import EventCompressionMode, FrameMappingMode
from calendar_anim.calendar.multi_frame.models import FrameUploadPlan, MultiFramePlan
from calendar_anim.calendar.recurrence_compaction.hybrid import (
    FINAL_HYBRID_RUN_ID,
    build_account_b_plan,
    build_hybrid_final_artifacts,
    frame_assignments,
)
from calendar_anim.calendar.recurrence_validation.ordering import OrderingCaptureResult
from calendar_anim.calendar.subcolumn_ordering import (
    SubcolumnOrderStrategy,
    summary_order_keys,
)
from calendar_anim.exceptions import CalendarAnimError


def source_plan() -> MultiFramePlan:
    start = date(2027, 10, 10)
    frames = [
        FrameUploadPlan(
            frame_index=index,
            source_timestamp_seconds=114 + index / 3,
            week_start=start + timedelta(weeks=index),
            frame_run_id=f"source-frame-{index:04d}",
            planned_events=1000 + index,
            artifact_directory=f"frames/frame-{index:04d}",
        )
        for index in range(108)
    ]
    events = [item.planned_events for item in frames]
    return MultiFramePlan(
        animation_id="cayde-final-3fps",
        run_id="cayde-final-126x72-3fps-36s-01",
        timezone="America/Sao_Paulo",
        source_file="input.mp4",
        clip_start_seconds=114,
        clip_end_seconds=150,
        clip_duration_seconds=36,
        output_fps=3,
        start_week=start,
        frame_start=0,
        frame_count=108,
        mapping_mode=FrameMappingMode.FULL_GRID,
        event_compression=EventCompressionMode.SYNCHRONIZED_HORIZONTAL_BANDS,
        palette_preset="cayde-final",
        background_color_id="1",
        foreground_color_ids=["1", "2", "3", "4"],
        target_grid_width=126,
        target_grid_height=72,
        grid_profile="high-detail-126x72",
        slots_per_day=18,
        vertical_step_minutes=15,
        visible_start_hour=6,
        visible_end_hour=24,
        subcolumn_order_strategy=SubcolumnOrderStrategy.ZERO_WIDTH,
        subcolumn_order_keys=summary_order_keys(18, SubcolumnOrderStrategy.ZERO_WIDTH),
        max_events_per_frame=5200,
        profile_ready=True,
        events_per_frame=events,
        total_events=sum(events),
        frames=frames,
    )


def test_frame_boundary_assigns_0_22_to_a_and_23_107_to_b() -> None:
    assignments = frame_assignments()

    assert [
        item.frame_index for item in assignments if item.calendar_profile == "account-a"
    ] == list(range(23))
    assert [
        item.frame_index for item in assignments if item.calendar_profile == "account-b"
    ] == list(range(23, 108))
    assert assignments[22].capture_zoom_percent == 33
    assert assignments[23].capture_zoom_percent == 90


def test_account_b_plan_recreates_whole_human_frame_24_and_never_splits_frames() -> None:
    source = source_plan()

    b_plan = build_account_b_plan(source)

    assert b_plan.run_id == FINAL_HYBRID_RUN_ID
    assert b_plan.calendar_profile == "account-b"
    assert b_plan.calendar_name == "Calendar Animation Lab B"
    assert b_plan.frame_start == 23
    assert b_plan.frame_count == 85
    assert [item.frame_index for item in b_plan.frames] == list(range(23, 108))
    assert all(item.calendar_profile == "account-b" for item in b_plan.frames)
    assert b_plan.frames[0].planned_events == source.frames[23].planned_events
    assert b_plan.total_events == sum(source.events_per_frame[23:])


def test_account_b_plan_is_deterministic() -> None:
    assert build_account_b_plan(source_plan()) == build_account_b_plan(source_plan())


def test_hybrid_artifacts_are_blocked_without_real_ordering_pass() -> None:
    result = OrderingCaptureResult(
        validation_id="recurrence-zero-width-ordering-account-b-01",
        calendar_profile="account-b",
        browser_zoom_percent=90,
        visible_window="06:00-00:00",
        snapshots=[],
        summaries_preserved_18_of_18=False,
        strict_x_ordering=False,
        recurring_equals_standalone=False,
        refresh_stable=False,
        navigation_stable=False,
        color_preserved=False,
        no_visible_text_pollution=True,
        result="NO-GO",
        comparison_path=str(Path("comparison.png")),
    )

    with pytest.raises(CalendarAnimError, match="gated"):
        build_hybrid_final_artifacts(None, result)  # type: ignore[arg-type]
