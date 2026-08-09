from datetime import date

import pytest

from calendar_anim.calendar.frame_mapping.models import EventCompressionMode, FrameMappingMode
from calendar_anim.calendar.multi_frame.planner import build_multi_frame_plan, frame_run_id
from calendar_anim.exceptions import CalendarAnimError
from calendar_anim.models.frame import AnimationFrame, Block
from tests.factories import make_manifest, make_ready_calibration_profile

pytestmark = pytest.mark.unit


def _manifest_with_frames(count: int):
    manifest = make_manifest(Block(x=0, y=0, width=1, color_id="1", color_hex="#33B679"))
    manifest.render.frame_count = count
    manifest.frames = [
        AnimationFrame(
            index=index,
            timestamp_seconds=float(index),
            image=f"frames/frame_{index:03d}.png",
            blocks=[Block(x=index % 4, y=0, width=1, color_id="1", color_hex="#33B679")],
        )
        for index in range(count)
    ]
    manifest.statistics.blocks = count
    return manifest


def test_plans_one_frame_with_normalized_week_and_deterministic_run_id() -> None:
    plan, frames = build_multi_frame_plan(
        _manifest_with_frames(1),
        make_ready_calibration_profile(),
        frame_start=0,
        frame_count=1,
        anchor_date=date(2026, 10, 7),
        run_id="animation-test-01",
        max_events_per_frame=1200,
    )

    assert plan.start_week == date(2026, 10, 4)
    assert plan.frames[0].week_start == date(2026, 10, 4)
    assert plan.frames[0].frame_run_id == "animation-test-01-frame-0000"
    assert frames[0].run_id == plan.frames[0].frame_run_id


def test_explicit_none_plans_six_uncompressed_frames_in_consecutive_weeks() -> None:
    plan, frames = build_multi_frame_plan(
        _manifest_with_frames(6),
        make_ready_calibration_profile(),
        frame_start=0,
        frame_count=6,
        anchor_date=date(2026, 10, 4),
        run_id="six-frames",
        max_events_per_frame=1200,
        mapping_mode=FrameMappingMode.FULL_GRID,
        event_compression=EventCompressionMode.NONE,
    )

    assert plan.frame_count == 6
    assert [frame.week_start for frame in plan.frames] == [
        date(2026, 10, 4),
        date(2026, 10, 11),
        date(2026, 10, 18),
        date(2026, 10, 25),
        date(2026, 11, 1),
        date(2026, 11, 8),
    ]
    assert [frame.frame_run_id for frame in plan.frames] == [
        f"six-frames-frame-{index:04d}" for index in range(6)
    ]
    assert plan.events_per_frame == [1008] * 6
    assert plan.total_events == 6048
    assert all(frame.event_count == 1008 for frame in frames)
    assert all(
        frame.subcolumn_order_keys == ["00", "01", "02", "03", "04", "05"] for frame in frames
    )


def test_multi_frame_plan_defaults_to_compressed_event_drafts_for_real_upload() -> None:
    plan, frames = build_multi_frame_plan(
        _manifest_with_frames(2),
        make_ready_calibration_profile(),
        frame_start=0,
        frame_count=2,
        anchor_date=date(2026, 11, 22),
        run_id="compressed-frames",
        max_events_per_frame=1200,
        mapping_mode=FrameMappingMode.FULL_GRID,
    )

    assert plan.event_compression is EventCompressionMode.SYNCHRONIZED_HORIZONTAL_BANDS
    assert all(frame.event_compression is plan.event_compression for frame in frames)
    assert all(frame.event_count < 1008 for frame in frames)
    assert plan.events_per_frame == [frame.event_count for frame in frames]
    assert plan.total_events == sum(frame.event_count for frame in frames)


def test_selected_frame_start_maps_to_first_requested_week() -> None:
    plan, _ = build_multi_frame_plan(
        _manifest_with_frames(8),
        make_ready_calibration_profile(),
        frame_start=2,
        frame_count=3,
        anchor_date=date(2026, 10, 4),
        run_id="selected",
        max_events_per_frame=1200,
    )

    assert [frame.frame_index for frame in plan.frames] == [2, 3, 4]
    assert plan.frames[0].week_start == date(2026, 10, 4)
    assert plan.frames[2].week_start == date(2026, 10, 18)


@pytest.mark.parametrize(
    ("frame_start", "frame_count", "message"),
    [
        (-1, 1, "frame start must be non-negative"),
        (0, 0, "frame count must be positive"),
        (2, 2, "exceeds manifest"),
    ],
)
def test_rejects_invalid_frame_ranges(frame_start: int, frame_count: int, message: str) -> None:
    with pytest.raises(CalendarAnimError, match=message):
        build_multi_frame_plan(
            _manifest_with_frames(3),
            make_ready_calibration_profile(),
            frame_start=frame_start,
            frame_count=frame_count,
            anchor_date=date(2026, 10, 4),
            run_id="invalid",
            max_events_per_frame=1200,
        )


def test_frame_run_id_stays_inside_calendar_identifier_limit() -> None:
    value = frame_run_id("a" * 64, 123)

    assert value.endswith("-frame-0123")
    assert len(value) == 64
    assert value != frame_run_id("a" * 63 + "b", 123)
