from collections.abc import Sequence
from datetime import date
from pathlib import Path

import pytest
from PIL import Image

from calendar_anim.calendar.fake import FakeCalendarGateway
from calendar_anim.calendar.frame_mapping.models import EventCompressionMode
from calendar_anim.calendar.lab import LabCalendarService
from calendar_anim.calendar.local_config import CalendarConfigStore
from calendar_anim.calendar.models import CalendarEventDraft, CalendarWriteResult
from calendar_anim.calendar.multi_frame.artifacts import (
    AnimationRunStore,
    initialize_animation_run,
)
from calendar_anim.calendar.multi_frame.models import FrameUploadStatus
from calendar_anim.calendar.multi_frame.planner import build_multi_frame_plan
from calendar_anim.calendar.multi_frame.service import MultiFrameUploadService
from calendar_anim.exceptions import CalendarAnimError
from calendar_anim.models.frame import AnimationFrame, Block
from tests.factories import make_manifest, make_ready_calibration_profile

pytestmark = pytest.mark.integration


class FailFrameOnceGateway(FakeCalendarGateway):
    def __init__(self, failing_frame: int) -> None:
        super().__init__()
        self.failing_frame = failing_frame
        self.failure_enabled = True

    def create_events(
        self, calendar_id: str, events: Sequence[CalendarEventDraft]
    ) -> CalendarWriteResult:
        frame_index = int(events[0].private_metadata["frame_index"])
        if self.failure_enabled and frame_index == self.failing_frame:
            first = super().create_events(calendar_id, events[:5])
            self.failure_enabled = False
            return CalendarWriteResult(
                created_event_ids=first.created_event_ids,
                created_event_indexes=first.created_event_indexes,
                failed_events=1,
                errors=["simulated frame failure"],
            )
        return super().create_events(calendar_id, events)


class RaisingGateway(FakeCalendarGateway):
    def __init__(self, fail_after_calls: int) -> None:
        super().__init__()
        self.fail_after_calls = fail_after_calls

    def create_events(
        self, calendar_id: str, events: Sequence[CalendarEventDraft]
    ) -> CalendarWriteResult:
        if self.create_event_calls >= self.fail_after_calls:
            raise RuntimeError("connection lost")
        return super().create_events(calendar_id, events)


class InterruptingGateway(FakeCalendarGateway):
    def create_events(
        self, calendar_id: str, events: Sequence[CalendarEventDraft]
    ) -> CalendarWriteResult:
        raise KeyboardInterrupt


def _initialized_run(tmp_path: Path, frame_count: int = 3):
    manifest = make_manifest(Block(x=0, y=0, width=1, color_id="1", color_hex="#33B679"))
    manifest.render.frame_count = frame_count
    manifest.frames = [
        AnimationFrame(
            index=index,
            timestamp_seconds=float(index),
            image=f"frames/frame_{index:03d}.png",
            blocks=[Block(x=index % 4, y=0, width=1, color_id="1", color_hex="#33B679")],
        )
        for index in range(frame_count)
    ]
    manifest.statistics.blocks = frame_count
    frames = tmp_path / "frames"
    frames.mkdir()
    for index in range(frame_count):
        Image.new("RGB", (4, 4), "#808080").save(frames / f"frame_{index:03d}.png")
    plan, frame_plans = build_multi_frame_plan(
        manifest,
        make_ready_calibration_profile(),
        frame_start=0,
        frame_count=frame_count,
        anchor_date=date(2026, 10, 4),
        run_id="resume-test",
        max_events_per_frame=1200,
        event_compression=EventCompressionMode.NONE,
    )
    store = AnimationRunStore(tmp_path / "runs")
    state = initialize_animation_run(
        plan, frame_plans, manifest, tmp_path / "animation.json", store
    )
    return plan, state, store


def _service(gateway: FakeCalendarGateway, store: AnimationRunStore, tmp_path: Path, **kwargs):
    return MultiFrameUploadService(
        gateway,
        LabCalendarService(gateway, CalendarConfigStore(tmp_path / "calendar.json")),
        store,
        **kwargs,
    )


def test_failure_stops_later_frames_and_resume_preserves_completed_frame(
    tmp_path: Path,
) -> None:
    plan, state, store = _initialized_run(tmp_path)
    gateway = FailFrameOnceGateway(failing_frame=1)
    progress: list[tuple[int, int, int]] = []
    service = _service(
        gateway,
        store,
        tmp_path,
        chunk_size=50,
        progress=lambda *value: progress.append(value),
    )

    first = service.upload(plan, state)

    assert [frame.status for frame in first.frames] == [
        FrameUploadStatus.COMPLETED,
        FrameUploadStatus.PARTIAL,
        FrameUploadStatus.PENDING,
    ]
    assert first.frames[0].created_events == 1008
    assert first.frames[1].created_events == 5
    calendar_id = first.calendar_id or ""
    frame_zero_ids = [
        event.id
        for event in gateway.events[calendar_id]
        if event.private_metadata["frame_index"] == "0"
    ]
    with pytest.raises(CalendarAnimError, match="Partial frame recovery required"):
        service.upload(plan, store.load_state(plan.run_id))

    resumed = service.upload(plan, store.load_state(plan.run_id), recover_partial=True)

    assert all(frame.status is FrameUploadStatus.COMPLETED for frame in resumed.frames)
    assert all(frame.created_events == 1008 for frame in resumed.frames)
    assert [
        event.id
        for event in gateway.events[calendar_id]
        if event.private_metadata["frame_index"] == "0"
    ] == frame_zero_ids
    assert len(gateway.events[calendar_id]) == 3 * 1008
    assert gateway.delete_event_calls == 1
    assert progress[0] == (0, 0, 1008)
    performance = store.load_performance(plan.run_id)
    assert len(performance.invocations) == 2
    assert performance.invocations[1].frames_previously_completed == [0]
    assert performance.invocations[1].frames_uploaded_this_invocation == [1, 2]
    assert [
        frame.frame_index for invocation in performance.invocations for frame in invocation.frames
    ].count(0) == 1


def test_exception_after_completed_chunk_checkpoints_partial_count(tmp_path: Path) -> None:
    plan, state, store = _initialized_run(tmp_path, frame_count=1)
    gateway = RaisingGateway(fail_after_calls=1)
    service = _service(gateway, store, tmp_path, chunk_size=10)

    with pytest.raises(RuntimeError, match="connection lost"):
        service.upload(plan, state)

    saved = store.load_state(plan.run_id)
    assert saved.frames[0].status is FrameUploadStatus.PARTIAL
    assert saved.frames[0].created_events == 10
    assert saved.frames[0].frame_completed_at is not None
    assert "connection lost" in saved.frames[0].errors
    performance = store.load_performance(plan.run_id)
    assert performance.invocations[0].frames[0].status is FrameUploadStatus.PARTIAL
    assert performance.invocations[0].frames[0].created_events == 10


def test_pending_state_with_remote_events_is_an_inconsistency(tmp_path: Path) -> None:
    plan, state, store = _initialized_run(tmp_path, frame_count=1)
    gateway = FakeCalendarGateway()
    calendar, _ = LabCalendarService(
        gateway, CalendarConfigStore(tmp_path / "calendar.json")
    ).resolve(plan.calendar_name, plan.timezone)
    frame_plan = store.load_frame_plan(plan, 0)
    gateway.create_events(calendar.id, frame_plan.events[:1])
    service = _service(gateway, store, tmp_path)

    with pytest.raises(CalendarAnimError, match="remote event"):
        service.upload(plan, state)

    assert store.load_state(plan.run_id).frames[0].status is FrameUploadStatus.PARTIAL


def test_failed_frame_with_no_remote_events_can_be_retried(tmp_path: Path) -> None:
    plan, state, store = _initialized_run(tmp_path, frame_count=1)
    gateway = RaisingGateway(fail_after_calls=0)
    service = _service(gateway, store, tmp_path, chunk_size=100)
    with pytest.raises(RuntimeError, match="connection lost"):
        service.upload(plan, state)
    assert store.load_state(plan.run_id).frames[0].status is FrameUploadStatus.FAILED

    gateway.fail_after_calls = 100
    resumed = service.upload(plan, store.load_state(plan.run_id))

    assert resumed.frames[0].status is FrameUploadStatus.COMPLETED
    assert resumed.frames[0].created_events == 1008


def test_keyboard_interrupt_never_marks_frame_completed(tmp_path: Path) -> None:
    plan, state, store = _initialized_run(tmp_path, frame_count=1)
    gateway = InterruptingGateway()
    service = _service(gateway, store, tmp_path)

    with pytest.raises(KeyboardInterrupt):
        service.upload(plan, state)

    saved = store.load_state(plan.run_id)
    assert saved.frames[0].status is FrameUploadStatus.PARTIAL
    assert saved.frames[0].created_events == 0
    assert "interrupted" in saved.frames[0].errors[0].lower()


def test_tampered_frame_week_is_rejected_before_event_creation(tmp_path: Path) -> None:
    plan, state, store = _initialized_run(tmp_path, frame_count=1)
    frame_path = store.frame_directory(plan, 0) / "frame-plan.json"
    serialized = frame_path.read_text(encoding="utf-8").replace("2026-10-04", "2026-10-11")
    frame_path.write_text(serialized, encoding="utf-8")
    gateway = FakeCalendarGateway()
    service = _service(gateway, store, tmp_path)

    with pytest.raises(CalendarAnimError, match="Frame week does not match"):
        service.upload(plan, state)

    assert gateway.create_event_calls == 0
