from datetime import date, timedelta

import pytest

from calendar_anim.calendar.frame_mapping.models import EventCompressionMode, FrameMappingMode
from calendar_anim.calendar.multi_frame.models import FrameUploadPlan, MultiFramePlan
from calendar_anim.calendar.recurrence_compaction.account_b_prefix import (
    ACCOUNT_B_PREFIX_RUN_ID,
    build_account_b_prefix_plan,
)
from calendar_anim.calendar.recurrence_compaction.models import RecurrenceSignature
from calendar_anim.calendar.recurrence_compaction.planner import (
    _build_parents,
    _Occurrence,
    _recurrence_groups,
)
from calendar_anim.calendar.subcolumn_ordering import (
    SubcolumnOrderStrategy,
    summary_order_keys,
)

pytestmark = pytest.mark.unit


def _source_plan() -> MultiFramePlan:
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
    events = [frame.planned_events for frame in frames]
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


def test_prefix_contains_only_frames_0_22_and_preserves_week_boundary() -> None:
    source = _source_plan()

    prefix = build_account_b_prefix_plan(source)

    assert prefix.run_id == ACCOUNT_B_PREFIX_RUN_ID
    assert prefix.calendar_profile == "account-b"
    assert prefix.calendar_name == "Calendar Animation Lab B"
    assert [frame.frame_index for frame in prefix.frames] == list(range(23))
    assert all(frame.calendar_profile == "account-b" for frame in prefix.frames)
    assert prefix.frames[0].week_start == date(2027, 10, 10)
    assert prefix.frames[-1].week_start == date(2028, 3, 12)
    assert source.frames[23].week_start == date(2028, 3, 19)
    assert (source.frames[23].week_start - prefix.frames[-1].week_start).days == 7
    assert {frame.week_start for frame in prefix.frames}.isdisjoint(
        frame.week_start for frame in source.frames[23:]
    )


def test_prefix_parent_namespace_cannot_collide_with_existing_b_run() -> None:
    signature = RecurrenceSignature(
        timezone="America/Sao_Paulo",
        day_of_week=0,
        local_start_time="08:00:00",
        duration_seconds=900,
        summary="\u200b",
        color_id="3",
    )
    starts = [
        _Occurrence(
            key=f"f{index:04d}:event",
            frame_index=index,
            start=_datetime(index),
            end=_datetime(index) + timedelta(minutes=15),
            role="foreground",
            original_event_id=f"event-{index}",
            signature=signature,
        )
        for index in range(3)
    ]
    groups = _recurrence_groups(starts)

    prefix = _build_parents(ACCOUNT_B_PREFIX_RUN_ID, "America/Sao_Paulo", groups, 100)
    existing = _build_parents(
        "cayde-final-hybrid-rdate-126x72-3fps-36s-01",
        "America/Sao_Paulo",
        groups,
        100,
    )

    assert {parent.parent_id for parent in prefix}.isdisjoint(
        parent.parent_id for parent in existing
    )
    assert [key for parent in prefix for key in parent.occurrence_keys] == [
        occurrence.key for occurrence in starts
    ]


def _datetime(week: int):  # type: ignore[no-untyped-def]
    from datetime import UTC, datetime

    return datetime(2027, 10, 11, 8, tzinfo=UTC) + timedelta(weeks=week)
