from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from PIL import Image

from calendar_anim.calendar.frame_mapping.models import EventCompressionMode
from calendar_anim.calendar.multi_frame.artifacts import (
    AnimationRunStore,
    initial_upload_state,
)
from calendar_anim.calendar.multi_frame.models import FrameUploadStatus
from calendar_anim.calendar.multi_frame.performance import (
    begin_upload_invocation,
    calculate_events_per_second,
    finish_upload_invocation,
    initial_performance_report,
    record_frame_performance,
)
from calendar_anim.calendar.multi_frame.planner import build_multi_frame_plan
from calendar_anim.models.frame import AnimationFrame, Block
from tests.factories import make_manifest, make_ready_calibration_profile

pytestmark = pytest.mark.unit


def _plan_and_state(tmp_path: Path):
    manifest = make_manifest(Block(x=0, y=0, width=1, color_id="1", color_hex="#33B679"))
    manifest.source.file_name = "benchmark.mp4"
    manifest.source.start_seconds = 115
    manifest.source.duration_seconds = 3
    manifest.render.output_fps = 3
    manifest.render.frame_count = 2
    manifest.frames = [
        AnimationFrame(
            index=index,
            timestamp_seconds=115 + index / 3,
            image=f"frames/frame_{index:03d}.png",
            blocks=[Block(x=index, y=0, width=1, color_id="1", color_hex="#33B679")],
        )
        for index in range(2)
    ]
    frames = tmp_path / "frames"
    frames.mkdir()
    for index in range(2):
        Image.new("RGB", (4, 4), "#808080").save(frames / f"frame_{index:03d}.png")
    plan, _ = build_multi_frame_plan(
        manifest,
        make_ready_calibration_profile(),
        frame_start=0,
        frame_count=2,
        anchor_date=date(2027, 8, 1),
        run_id="performance-test",
        max_events_per_frame=1200,
        event_compression=EventCompressionMode.NONE,
    )
    return plan, initial_upload_state(plan)


def test_performance_report_serializes_to_json_and_text(tmp_path: Path) -> None:
    plan, state = _plan_and_state(tmp_path)
    store = AnimationRunStore(tmp_path / "runs")
    report = initial_performance_report(plan, state)

    json_path, text_path = store.save_performance(report)
    loaded = store.load_performance(plan.run_id)

    assert loaded == report
    assert '"total_planned_events": 2016' in json_path.read_text(encoding="utf-8")
    text = text_path.read_text(encoding="utf-8")
    assert "Cutscene Upload Performance" in text
    assert "Clip: 115.000-118.000 seconds" in text


def test_events_per_second_handles_zero_elapsed_and_zero_events() -> None:
    assert calculate_events_per_second(10, 2) == 5
    assert calculate_events_per_second(0, 2) == 0
    assert calculate_events_per_second(10, 0) is None
    assert calculate_events_per_second(0, 0) is None


def test_failed_frame_is_recorded_and_aggregated(tmp_path: Path) -> None:
    plan, state = _plan_and_state(tmp_path)
    report = initial_performance_report(plan, state)
    started = datetime(2027, 8, 1, tzinfo=UTC)
    invocation = begin_upload_invocation(report, state, started)
    frame = state.frames[0]
    frame.status = FrameUploadStatus.PARTIAL
    frame.created_events = 5
    frame.failed_events = 1
    frame.frame_started_at = started
    frame.frame_completed_at = started + timedelta(seconds=2)
    frame.duration_seconds = 2

    recorded = record_frame_performance(invocation, plan, frame)
    finish_upload_invocation(
        report,
        plan,
        state,
        invocation,
        finished_at=started + timedelta(seconds=3),
        elapsed_seconds=3,
        status="stopped",
    )

    assert recorded.status is FrameUploadStatus.PARTIAL
    assert recorded.failed_events == 1
    assert recorded.events_per_second == 2.5
    assert report.total_created_events == 5
    assert report.total_failed_events == 1
    assert report.overall_events_per_second == pytest.approx(5 / 3)


def test_resume_does_not_double_count_completed_frame(tmp_path: Path) -> None:
    plan, state = _plan_and_state(tmp_path)
    completed = state.frames[0]
    completed.status = FrameUploadStatus.COMPLETED
    completed.created_events = completed.planned_events
    completed.duration_seconds = 10
    report = initial_performance_report(plan, state)
    started = datetime(2027, 8, 1, tzinfo=UTC)
    invocation = begin_upload_invocation(report, state, started)
    uploaded = state.frames[1]
    uploaded.status = FrameUploadStatus.COMPLETED
    uploaded.created_events = uploaded.planned_events
    uploaded.duration_seconds = 20
    record_frame_performance(invocation, plan, uploaded)

    finish_upload_invocation(
        report,
        plan,
        state,
        invocation,
        finished_at=started + timedelta(seconds=20),
        elapsed_seconds=20,
        status="completed",
    )

    assert invocation.frames_previously_completed == [0]
    assert invocation.frames_uploaded_this_invocation == [1]
    assert [frame.frame_index for frame in invocation.frames] == [1]
    assert report.total_created_events == uploaded.planned_events
    assert report.average_seconds_per_frame == 20
