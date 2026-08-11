from collections.abc import Sequence
from datetime import date
from pathlib import Path

import pytest
from PIL import Image

from calendar_anim.calendar.fake import FakeCalendarGateway
from calendar_anim.calendar.frame_mapping.models import EventCompressionMode
from calendar_anim.calendar.lab import LabCalendarService
from calendar_anim.calendar.local_config import CalendarConfigStore
from calendar_anim.calendar.models import (
    CalendarEventDraft,
    CalendarWriteFailure,
    CalendarWriteResult,
)
from calendar_anim.calendar.multi_frame.artifacts import (
    AnimationRunStore,
    initialize_animation_run,
)
from calendar_anim.calendar.multi_frame.models import FrameUploadStatus
from calendar_anim.calendar.multi_frame.planner import build_multi_frame_plan
from calendar_anim.calendar.multi_frame.retry import UploadRetryPolicy
from calendar_anim.calendar.multi_frame.service import MultiFrameUploadService
from calendar_anim.exceptions import CalendarAnimError
from calendar_anim.models.frame import AnimationFrame, Block
from tests.factories import make_manifest, make_ready_calibration_profile

pytestmark = pytest.mark.integration


class PartialRetryableOnceGateway(FakeCalendarGateway):
    def __init__(self) -> None:
        super().__init__()
        self.failure_enabled = True

    def create_events(
        self, calendar_id: str, events: Sequence[CalendarEventDraft]
    ) -> CalendarWriteResult:
        if self.failure_enabled:
            first = super().create_events(calendar_id, events[:5])
            self.failure_enabled = False
            failures = [
                CalendarWriteFailure(
                    event_index=index,
                    message="simulated 503",
                    retryable=True,
                    status_code=503,
                )
                for index in range(5, len(events))
            ]
            return CalendarWriteResult(
                created_event_ids=first.created_event_ids,
                created_event_indexes=first.created_event_indexes,
                failed_events=len(failures),
                errors=["simulated frame failure"],
                failures=failures,
            )
        return super().create_events(calendar_id, events)


class RetryableGateway(FakeCalendarGateway):
    def __init__(self, failures: int) -> None:
        super().__init__()
        self.failures = failures

    def create_events(
        self, calendar_id: str, events: Sequence[CalendarEventDraft]
    ) -> CalendarWriteResult:
        if self.failures:
            self.failures -= 1
            self.create_event_calls += 1
            raise TimeoutError("connection lost")
        return super().create_events(calendar_id, events)


class PermanentFailureGateway(FakeCalendarGateway):
    def create_events(
        self, calendar_id: str, events: Sequence[CalendarEventDraft]
    ) -> CalendarWriteResult:
        self.create_event_calls += 1
        return CalendarWriteResult(
            failed_events=len(events),
            errors=["simulated 400 invalid request"],
            failures=[
                CalendarWriteFailure(
                    event_index=index,
                    message="simulated 400 invalid request",
                    retryable=False,
                    status_code=400,
                )
                for index in range(len(events))
            ],
        )


class PersistThenCrashGateway(FakeCalendarGateway):
    def __init__(self) -> None:
        super().__init__()
        self.crash = True

    def create_events(
        self, calendar_id: str, events: Sequence[CalendarEventDraft]
    ) -> CalendarWriteResult:
        result = super().create_events(calendar_id, events)
        if self.crash:
            self.crash = False
            raise TimeoutError("response lost after persistence")
        return result


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
        sleeper=lambda _: None,
        jitter=lambda _: 0.0,
        **kwargs,
    )


def test_retryable_partial_chunk_retries_only_missing_events_without_duplicates(
    tmp_path: Path,
) -> None:
    plan, state, store = _initialized_run(tmp_path, frame_count=1)
    gateway = PartialRetryableOnceGateway()
    progress: list[tuple[int, int, int]] = []
    service = _service(
        gateway,
        store,
        tmp_path,
        chunk_size=50,
        progress=lambda *value: progress.append(value),
    )

    uploaded = service.upload(plan, state)

    assert uploaded.frames[0].status is FrameUploadStatus.COMPLETED
    assert uploaded.frames[0].event_retry_count == 1
    calendar_id = uploaded.calendar_id or ""
    assert len(gateway.events[calendar_id]) == 1008
    assert len({event.id for event in gateway.events[calendar_id]}) == 1008
    assert gateway.delete_event_calls == 0
    assert progress[0] == (0, 0, 1008)
    performance = store.load_performance(plan.run_id)
    assert performance.frames[0].event_retry_count == 1
    attempt = performance.invocations[0].frames[0]
    assert attempt.initial_attempt_seconds is not None
    assert attempt.total_frame_elapsed_seconds is not None
    assert len(attempt.attempts) == 1


def test_response_loss_after_persistence_is_idempotent(tmp_path: Path) -> None:
    plan, state, store = _initialized_run(tmp_path, frame_count=1)
    gateway = PersistThenCrashGateway()
    service = _service(gateway, store, tmp_path, chunk_size=10)

    uploaded = service.upload(plan, state)

    calendar_id = uploaded.calendar_id or ""
    assert uploaded.frames[0].status is FrameUploadStatus.COMPLETED
    assert uploaded.frames[0].event_retry_count == 1
    assert len(gateway.events[calendar_id]) == 1008
    assert len({event.id for event in gateway.events[calendar_id]}) == 1008


def test_partial_state_reconciles_remote_events_and_creates_only_missing(tmp_path: Path) -> None:
    plan, state, store = _initialized_run(tmp_path, frame_count=1)
    gateway = FakeCalendarGateway()
    calendar, _ = LabCalendarService(
        gateway, CalendarConfigStore(tmp_path / "calendar.json")
    ).resolve(plan.calendar_name, plan.timezone)
    frame_plan = store.load_frame_plan(plan, 0)
    gateway.create_events(calendar.id, frame_plan.events[:1])
    state.calendar_id = calendar.id
    state.frames[0].status = FrameUploadStatus.PARTIAL
    store.save_state(state)
    service = _service(gateway, store, tmp_path)

    uploaded = service.upload(plan, store.load_state(plan.run_id))

    assert uploaded.frames[0].status is FrameUploadStatus.COMPLETED
    assert len(gateway.events[calendar.id]) == 1008
    assert gateway.delete_event_calls == 0


def test_retryable_failure_exhaustion_stops_with_persisted_failed_state(tmp_path: Path) -> None:
    plan, state, store = _initialized_run(tmp_path, frame_count=1)
    gateway = RetryableGateway(failures=100)
    service = _service(
        gateway,
        store,
        tmp_path,
        chunk_size=100,
        retry_policy=UploadRetryPolicy(max_event_attempts=2, max_frame_recovery_cycles=1),
    )
    with pytest.raises(CalendarAnimError, match="failed after"):
        service.upload(plan, state)

    saved = store.load_state(plan.run_id)
    assert saved.frames[0].status is FrameUploadStatus.FAILED
    assert saved.frames[0].event_retry_count == 2
    assert saved.frames[0].recovery_cycles == 1
    assert saved.frames[0].last_failure_retryable is True
    performance = store.load_performance(plan.run_id)
    measured = performance.invocations[0].frames[0]
    assert len(measured.attempts) == 2
    assert measured.initial_attempt_seconds == measured.attempts[0].elapsed_seconds
    assert measured.total_frame_elapsed_seconds == saved.frames[0].duration_seconds


def test_non_retryable_failure_does_not_loop(tmp_path: Path) -> None:
    plan, state, store = _initialized_run(tmp_path, frame_count=1)
    gateway = PermanentFailureGateway()
    service = _service(gateway, store, tmp_path, chunk_size=100)

    with pytest.raises(CalendarAnimError, match="failed after 0 event retries"):
        service.upload(plan, state)

    assert gateway.create_event_calls == 1
    saved = store.load_state(plan.run_id)
    assert saved.frames[0].status is FrameUploadStatus.FAILED
    assert saved.frames[0].last_failure_retryable is False


def test_resume_skips_completed_frames_and_preserves_atomic_checkpoint(tmp_path: Path) -> None:
    plan, state, store = _initialized_run(tmp_path, frame_count=2)
    gateway = FakeCalendarGateway()
    service = _service(gateway, store, tmp_path)

    service.upload(plan, state)
    calls_after_first_upload = gateway.create_event_calls
    resumed = service.upload(plan, store.load_state(plan.run_id))

    assert all(frame.status is FrameUploadStatus.COMPLETED for frame in resumed.frames)
    assert gateway.create_event_calls == calls_after_first_upload
    assert not store.state_path(plan.run_id).with_name(".animation-state.json.tmp").exists()
    performance = store.load_performance(plan.run_id)
    assert performance.invocations[-1].frames_previously_completed == [0, 1]
    assert performance.invocations[-1].frames_uploaded_this_invocation == []


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
