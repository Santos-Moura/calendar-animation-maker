from datetime import UTC, date, datetime
from pathlib import Path
from unittest.mock import patch

import pytest
from PIL import Image

from calendar_anim.calendar.multi_frame.artifacts import (
    AnimationRunStore,
    build_animation_report,
    initial_upload_state,
    initialize_animation_run,
)
from calendar_anim.calendar.multi_frame.models import FrameUploadStatus
from calendar_anim.calendar.multi_frame.planner import build_multi_frame_plan
from calendar_anim.exceptions import CalendarAnimError
from calendar_anim.models.frame import AnimationFrame, Block
from tests.factories import make_manifest, make_ready_calibration_profile

pytestmark = pytest.mark.unit


def _planned_run(tmp_path: Path):
    manifest = make_manifest(Block(x=0, y=0, width=1, color_id="1", color_hex="#33B679"))
    manifest.render.frame_count = 2
    manifest.frames = [
        AnimationFrame(
            index=index,
            timestamp_seconds=float(index),
            image=f"frames/frame_{index:03d}.png",
            blocks=[Block(x=index, y=0, width=1, color_id="1", color_hex="#33B679")],
        )
        for index in range(2)
    ]
    manifest.statistics.blocks = 2
    frames_dir = tmp_path / "frames"
    frames_dir.mkdir()
    for index in range(2):
        Image.new("RGB", (4, 4), "#808080").save(frames_dir / f"frame_{index:03d}.png")
    plan, frame_plans = build_multi_frame_plan(
        manifest,
        make_ready_calibration_profile(),
        frame_start=0,
        frame_count=2,
        anchor_date=date(2026, 10, 4),
        run_id="artifact-test",
        max_events_per_frame=1200,
    )
    return manifest, plan, frame_plans


def test_initial_state_is_pending_and_atomic_state_round_trips(tmp_path: Path) -> None:
    _, plan, _ = _planned_run(tmp_path)
    store = AnimationRunStore(tmp_path / "runs")
    state = initial_upload_state(plan)

    with patch(
        "calendar_anim.calendar.multi_frame.artifacts.os.replace",
        wraps=__import__("os").replace,
    ) as replace:
        path = store.save_state(state)

    assert replace.call_count == 1
    assert not path.with_name(f".{path.name}.tmp").exists()
    loaded = store.load_state(plan.run_id)
    assert [frame.status for frame in loaded.frames] == [
        FrameUploadStatus.PENDING,
        FrameUploadStatus.PENDING,
    ]


def test_state_persists_completed_partial_and_failed(tmp_path: Path) -> None:
    _, plan, _ = _planned_run(tmp_path)
    store = AnimationRunStore(tmp_path / "runs")
    state = initial_upload_state(plan)
    state.frames[0].status = FrameUploadStatus.COMPLETED
    state.frames[0].created_events = state.frames[0].planned_events
    state.frames[0].frame_started_at = datetime(2026, 10, 4, tzinfo=UTC)
    state.frames[0].frame_completed_at = datetime(2026, 10, 4, 0, 10, tzinfo=UTC)
    state.frames[0].duration_seconds = 600
    state.frames[1].status = FrameUploadStatus.PARTIAL
    state.frames[1].created_events = 600
    state.frames[1].failed_events = 1
    store.save_state(state)

    loaded = store.load_state(plan.run_id)
    assert loaded.frames[0].status is FrameUploadStatus.COMPLETED
    assert loaded.frames[1].status is FrameUploadStatus.PARTIAL
    loaded.frames[1].status = FrameUploadStatus.FAILED
    store.save_state(loaded)
    assert store.load_state(plan.run_id).frames[1].status is FrameUploadStatus.FAILED


def test_initialization_writes_immutable_plan_state_report_and_frame_artifacts(
    tmp_path: Path,
) -> None:
    manifest, plan, frame_plans = _planned_run(tmp_path)
    store = AnimationRunStore(tmp_path / "runs")
    state = initialize_animation_run(
        plan, frame_plans, manifest, tmp_path / "animation.json", store
    )

    run_dir = store.run_directory(plan.run_id)
    assert (run_dir / "animation-plan.json").is_file()
    assert (run_dir / "animation-state.json").is_file()
    assert (run_dir / "animation-report.txt").is_file()
    for index in range(2):
        frame_dir = run_dir / "frames" / f"frame-{index:04d}"
        assert (frame_dir / "frame-plan.json").is_file()
        assert (frame_dir / "mapped-preview.png").is_file()
        result = (frame_dir / "execution-result.json").read_text(encoding="utf-8")
        assert '"status": "pending"' in result
    assert state.frames[0].status is FrameUploadStatus.PENDING
    assert "Total events: 2016" in build_animation_report(plan, state)


def test_existing_plan_cannot_be_changed(tmp_path: Path) -> None:
    _, plan, _ = _planned_run(tmp_path)
    store = AnimationRunStore(tmp_path / "runs")
    store.save_plan(plan)
    changed = plan.model_copy(update={"total_events": plan.total_events + 1})

    with pytest.raises(CalendarAnimError, match="different content"):
        store.save_plan(changed)
