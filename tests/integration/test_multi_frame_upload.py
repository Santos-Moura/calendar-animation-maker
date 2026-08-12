from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
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
    CalendarWritePacingSnapshot,
    CalendarWriteResult,
)
from calendar_anim.calendar.multi_frame.artifacts import (
    AnimationRunStore,
    initialize_animation_run,
)
from calendar_anim.calendar.multi_frame.models import (
    FrameUploadStatus,
    UploadPauseReason,
)
from calendar_anim.calendar.multi_frame.planner import build_multi_frame_plan
from calendar_anim.calendar.multi_frame.quota_wait import QuotaWaitPolicy
from calendar_anim.calendar.multi_frame.retry import UploadRetryPolicy
from calendar_anim.calendar.multi_frame.service import (
    CalendarUsageQuotaPause,
    MultiFrameUploadService,
    normalize_legacy_calendar_usage_quota_pause,
)
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


class RateLimitOnceGateway(FakeCalendarGateway):
    def __init__(self, status_code: int) -> None:
        super().__init__()
        self.status_code = status_code
        self.rate_limited = False

    def create_events(
        self, calendar_id: str, events: Sequence[CalendarEventDraft]
    ) -> CalendarWriteResult:
        if not self.rate_limited:
            self.rate_limited = True
            self.create_event_calls += 1
            return CalendarWriteResult(
                failed_events=len(events),
                errors=["simulated rateLimitExceeded"],
                failures=[
                    CalendarWriteFailure(
                        event_index=index,
                        message="simulated rateLimitExceeded",
                        retryable=True,
                        status_code=self.status_code,
                        reason="rateLimitExceeded",
                    )
                    for index in range(len(events))
                ],
                rate_limit_exceeded_count=1,
            )
        return super().create_events(calendar_id, events)


class QuotaAfterSuccessfulCreationsGateway(FakeCalendarGateway):
    def __init__(self, quota_frame_index: int = 1, successes_before_quota: int = 7) -> None:
        super().__init__()
        self.quota_frame_index = quota_frame_index
        self.successes_before_quota = successes_before_quota
        self.quota_enabled = True
        self.submitted_lengths: list[tuple[int, int]] = []

    def create_events(
        self, calendar_id: str, events: Sequence[CalendarEventDraft]
    ) -> CalendarWriteResult:
        frame_index = events[0].frame_index if events else -1
        assert frame_index is not None
        self.submitted_lengths.append((frame_index, len(events)))
        if frame_index == self.quota_frame_index and self.quota_enabled:
            self.quota_enabled = False
            successful = super().create_events(calendar_id, events[: self.successes_before_quota])
            failures = [
                CalendarWriteFailure(
                    event_index=index,
                    message="Calendar usage limits exceeded",
                    retryable=False,
                    status_code=403,
                    reason="quotaExceeded",
                )
                for index in range(self.successes_before_quota, len(events))
            ]
            return CalendarWriteResult(
                created_event_ids=successful.created_event_ids,
                created_event_indexes=successful.created_event_indexes,
                failed_events=len(failures),
                errors=["Calendar usage limits exceeded (quotaExceeded)"],
                failures=failures,
                quota_exceeded_count=1,
                quota_circuit_breaker_count=1,
            )
        return super().create_events(calendar_id, events)


class AutomaticQuotaGateway(FakeCalendarGateway):
    def __init__(self, quota_failures: int, current_interval_seconds: float = 2.25) -> None:
        super().__init__()
        self.quota_failures = quota_failures
        self.submitted_lengths: list[int] = []
        self.pacing = CalendarWritePacingSnapshot(
            minimum_interval_seconds=0.75,
            current_interval_seconds=current_interval_seconds,
            maximum_interval_seconds=3.0,
            successful_writes_since_rate_limit=17,
        )
        self.restored_pacing: list[CalendarWritePacingSnapshot] = []

    def create_events(
        self, calendar_id: str, events: Sequence[CalendarEventDraft]
    ) -> CalendarWriteResult:
        self.submitted_lengths.append(len(events))
        if self.quota_failures:
            self.quota_failures -= 1
            self.create_event_calls += 1
            return CalendarWriteResult(
                failed_events=len(events),
                errors=["Calendar usage limits exceeded (quotaExceeded)"],
                failures=[
                    CalendarWriteFailure(
                        event_index=index,
                        message="Calendar usage limits exceeded",
                        retryable=False,
                        status_code=403,
                        reason="quotaExceeded",
                    )
                    for index in range(len(events))
                ],
                quota_exceeded_count=1,
                quota_circuit_breaker_count=1,
            )
        return super().create_events(calendar_id, events)

    def write_pacing_snapshot(self) -> CalendarWritePacingSnapshot:
        return self.pacing

    def restore_write_pacing(self, snapshot: CalendarWritePacingSnapshot) -> None:
        self.pacing = snapshot
        self.restored_pacing.append(snapshot)


class FakeUploadClock:
    def __init__(self) -> None:
        self.current = datetime(2026, 8, 12, tzinfo=UTC)
        self.elapsed = 0.0
        self.sleeps: list[float] = []
        self.interrupt_next_sleep = False

    def now(self) -> datetime:
        return self.current

    def monotonic(self) -> float:
        return self.elapsed

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        if self.interrupt_next_sleep:
            self.interrupt_next_sleep = False
            raise KeyboardInterrupt
        self.advance(seconds)

    def advance(self, seconds: float) -> None:
        self.elapsed += seconds
        self.current += timedelta(seconds=seconds)


def _quota_policy(*, max_auto_wait_seconds: float = 48 * 60 * 60) -> QuotaWaitPolicy:
    return QuotaWaitPolicy(
        cooldown_seconds=(900.0, 1800.0, 3600.0, 7200.0, 14400.0),
        jitter_seconds=0.0,
        max_auto_wait_seconds=max_auto_wait_seconds,
        conservative_recovery_interval_seconds=1.5,
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
    sleeper = kwargs.pop("sleeper", lambda _: None)
    return MultiFrameUploadService(
        gateway,
        LabCalendarService(gateway, CalendarConfigStore(tmp_path / "calendar.json")),
        store,
        sleeper=sleeper,
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


@pytest.mark.parametrize("status_code", [403, 429])
def test_rate_limit_uses_long_cooldown_then_resumes_missing_events(
    tmp_path: Path, status_code: int
) -> None:
    plan, state, store = _initialized_run(tmp_path, frame_count=1)
    gateway = RateLimitOnceGateway(status_code)
    sleeps: list[float] = []
    service = _service(
        gateway,
        store,
        tmp_path,
        chunk_size=50,
        sleeper=sleeps.append,
    )

    uploaded = service.upload(plan, state)

    assert uploaded.frames[0].status is FrameUploadStatus.COMPLETED
    assert uploaded.frames[0].event_retry_count == 1
    assert uploaded.frames[0].rate_limit_exceeded_count == 1
    assert uploaded.frames[0].adaptive_rate_limit_cooldowns == 1
    assert uploaded.frames[0].quota_exceeded_count == 0
    assert sleeps[0] == 32.0
    calendar_id = uploaded.calendar_id or ""
    assert len(gateway.events[calendar_id]) == 1008
    performance = store.load_performance(plan.run_id)
    assert performance.rate_limit_exceeded_count == 1
    assert performance.adaptive_rate_limit_cooldowns == 1
    assert performance.quota_exceeded_count == 0


def test_quota_circuit_breaker_preserves_partial_frame_without_cleanup(
    tmp_path: Path,
) -> None:
    plan, state, store = _initialized_run(tmp_path, frame_count=2)
    gateway = QuotaAfterSuccessfulCreationsGateway()
    service = _service(gateway, store, tmp_path, chunk_size=50)

    with pytest.raises(CalendarUsageQuotaPause) as raised:
        service.upload(plan, state)

    saved = store.load_state(plan.run_id)
    assert saved.frames[0].status is FrameUploadStatus.COMPLETED
    assert saved.frames[1].status is FrameUploadStatus.PARTIAL
    assert saved.frames[1].created_events == 7
    assert saved.frames[1].failed_events == 0
    assert saved.frames[1].quota_exceeded_count == 1
    assert saved.frames[1].quota_circuit_breaker_count == 1
    assert saved.frames[1].event_retry_count == 0
    assert saved.frames[1].recovery_cycles == 0
    assert saved.pause is not None
    assert saved.pause.reason is UploadPauseReason.CALENDAR_USAGE_QUOTA_EXCEEDED
    assert saved.pause.http_status == 403
    assert saved.pause.google_reason == "quotaExceeded"
    assert saved.pause.frame_index == 1
    assert saved.pause.created_before_pause == 7
    assert saved.pause.remaining_events == 1001
    assert raised.value.pause == saved.pause
    assert gateway.delete_event_calls == 0
    assert sum(frame == 1 for frame, _ in gateway.submitted_lengths) == 1
    assert not store.state_path(plan.run_id).with_name(".animation-state.json.tmp").exists()

    performance = store.load_performance(plan.run_id)
    assert performance.invocations[-1].status == "stopped"
    assert performance.quota_exceeded_count == 1
    assert performance.quota_circuit_breaker_count == 1
    assert performance.rate_limit_exceeded_count == 0
    assert performance.adaptive_rate_limit_cooldowns == 0
    assert performance.pause == saved.pause
    assert performance.pause_history == saved.pause_history


def test_resume_after_quota_pause_reconciles_only_missing_events(tmp_path: Path) -> None:
    plan, state, store = _initialized_run(tmp_path, frame_count=2)
    gateway = QuotaAfterSuccessfulCreationsGateway()
    service = _service(gateway, store, tmp_path, chunk_size=50)

    with pytest.raises(CalendarUsageQuotaPause):
        service.upload(plan, state)

    submissions_before_resume = len(gateway.submitted_lengths)
    completed_frame_calls_before_resume = sum(frame == 0 for frame, _ in gateway.submitted_lengths)
    resumed = service.upload(plan, store.load_state(plan.run_id))
    resume_submissions = gateway.submitted_lengths[submissions_before_resume:]
    calendar_id = resumed.calendar_id or ""

    assert all(frame.status is FrameUploadStatus.COMPLETED for frame in resumed.frames)
    assert resumed.pause is None
    assert len(resumed.pause_history) == 1
    assert sum(frame == 0 for frame, _ in gateway.submitted_lengths) == (
        completed_frame_calls_before_resume
    )
    assert all(frame == 1 for frame, _ in resume_submissions)
    assert sum(length for _, length in resume_submissions) == 1001
    assert len(gateway.events[calendar_id]) == 2016
    assert len({event.id for event in gateway.events[calendar_id]}) == 2016
    assert gateway.delete_event_calls == 0

    performance = store.load_performance(plan.run_id)
    invocation = performance.invocations[-1]
    assert invocation.frames_previously_completed == [0]
    assert invocation.frames_uploaded_this_invocation == [1]
    assert performance.pause is None
    assert len(performance.pause_history) == 1
    assert performance.quota_exceeded_count == 1
    assert performance.quota_circuit_breaker_count == 1


def test_legacy_quota_failure_is_normalized_to_partial_pause(tmp_path: Path) -> None:
    _plan, state, _store = _initialized_run(tmp_path, frame_count=1)
    frame = state.frames[0]
    frame.status = FrameUploadStatus.FAILED
    frame.created_events = 12
    frame.failed_events = 3
    frame.errors = ["403 quotaExceeded: Calendar usage limits exceeded"]

    changed = normalize_legacy_calendar_usage_quota_pause(state)

    assert changed is True
    assert frame.status is FrameUploadStatus.PARTIAL
    assert frame.created_events == 12
    assert frame.failed_events == 0
    assert frame.quota_exceeded_count == 1
    assert frame.quota_circuit_breaker_count == 1
    assert state.pause is not None
    assert state.pause.frame_index == 0
    assert state.pause.created_before_pause == 12


def test_automatic_quota_wait_progression_recovers_without_duplicates(tmp_path: Path) -> None:
    plan, state, store = _initialized_run(tmp_path, frame_count=1)
    gateway = AutomaticQuotaGateway(quota_failures=5)
    clock = FakeUploadClock()
    waits: list[tuple[int, float]] = []
    service = _service(
        gateway,
        store,
        tmp_path,
        chunk_size=50,
        now=clock.now,
        clock=clock.monotonic,
        sleeper=clock.sleep,
        quota_wait_policy=_quota_policy(),
        quota_wait_callback=lambda wait, remaining: waits.append((wait.stage_index, remaining)),
    )

    uploaded = service.upload(plan, state)

    assert uploaded.frames[0].status is FrameUploadStatus.COMPLETED
    assert clock.sleeps == [900.0, 1800.0, 3600.0, 7200.0, 14400.0]
    assert waits == [
        (0, 900.0),
        (1, 1800.0),
        (2, 3600.0),
        (3, 7200.0),
        (4, 14400.0),
    ]
    assert gateway.submitted_lengths[:6] == [50, 1, 1, 1, 1, 1]
    assert uploaded.quota_wait is None
    assert uploaded.pause is None
    assert uploaded.quota_wait_entries == 1
    assert uploaded.quota_wait_attempts == 5
    assert uploaded.quota_recoveries == 1
    assert uploaded.quota_wait_total_seconds == 27900.0
    assert uploaded.largest_quota_cooldown_seconds == 14400.0
    assert len(uploaded.pause_history) == 5
    assert uploaded.write_pacing is not None
    assert uploaded.write_pacing.current_interval_seconds == 2.25
    calendar_id = uploaded.calendar_id or ""
    assert len(gateway.events[calendar_id]) == 1008
    assert len({event.id for event in gateway.events[calendar_id]}) == 1008
    assert gateway.delete_event_calls == 0

    performance = store.load_performance(plan.run_id)
    assert performance.quota_wait_entries == 1
    assert performance.quota_wait_attempts == 5
    assert performance.quota_recoveries == 1
    assert performance.quota_wait_total_seconds == 27900.0
    assert performance.wall_clock_elapsed_seconds == 27900.0
    assert performance.active_upload_elapsed_seconds == 0.0


def test_ctrl_c_during_quota_wait_checkpoints_absolute_retry(tmp_path: Path) -> None:
    plan, state, store = _initialized_run(tmp_path, frame_count=1)
    gateway = AutomaticQuotaGateway(quota_failures=1)
    clock = FakeUploadClock()
    clock.interrupt_next_sleep = True
    service = _service(
        gateway,
        store,
        tmp_path,
        now=clock.now,
        clock=clock.monotonic,
        sleeper=clock.sleep,
        quota_wait_policy=_quota_policy(),
    )

    with pytest.raises(KeyboardInterrupt):
        service.upload(plan, state)

    saved = store.load_state(plan.run_id)
    assert saved.frames[0].status is FrameUploadStatus.PARTIAL
    assert saved.quota_wait is not None
    assert saved.quota_wait.next_retry_at == clock.current + timedelta(minutes=15)
    assert saved.quota_wait_total_seconds == 0
    assert gateway.create_event_calls == 1


def test_quota_recovery_raises_low_pacing_to_conservative_interval(tmp_path: Path) -> None:
    plan, state, store = _initialized_run(tmp_path, frame_count=1)
    gateway = AutomaticQuotaGateway(quota_failures=1, current_interval_seconds=0.75)
    clock = FakeUploadClock()
    uploaded = _service(
        gateway,
        store,
        tmp_path,
        now=clock.now,
        clock=clock.monotonic,
        sleeper=clock.sleep,
        quota_wait_policy=_quota_policy(),
    ).upload(plan, state)

    assert uploaded.write_pacing is not None
    assert uploaded.write_pacing.current_interval_seconds == 1.5
    assert uploaded.write_pacing.successful_writes_since_rate_limit == 0


def test_restart_during_quota_cooldown_waits_then_probes_one_event(tmp_path: Path) -> None:
    plan, state, store = _initialized_run(tmp_path, frame_count=1)
    gateway = AutomaticQuotaGateway(quota_failures=1)
    clock = FakeUploadClock()
    clock.interrupt_next_sleep = True
    first = _service(
        gateway,
        store,
        tmp_path,
        now=clock.now,
        clock=clock.monotonic,
        sleeper=clock.sleep,
        quota_wait_policy=_quota_policy(),
    )
    with pytest.raises(KeyboardInterrupt):
        first.upload(plan, state)

    clock.sleeps.clear()
    resumed = _service(
        gateway,
        store,
        tmp_path,
        now=clock.now,
        clock=clock.monotonic,
        sleeper=clock.sleep,
        quota_wait_policy=_quota_policy(),
    ).upload(plan, store.load_state(plan.run_id))

    assert clock.sleeps[0] == 900.0
    assert gateway.submitted_lengths[:2] == [50, 1]
    assert resumed.frames[0].status is FrameUploadStatus.COMPLETED
    assert resumed.quota_recoveries == 1
    assert gateway.restored_pacing
    assert gateway.restored_pacing[0].current_interval_seconds == 2.25


def test_restart_after_quota_cooldown_probes_immediately(tmp_path: Path) -> None:
    plan, state, store = _initialized_run(tmp_path, frame_count=1)
    gateway = AutomaticQuotaGateway(quota_failures=1)
    clock = FakeUploadClock()
    clock.interrupt_next_sleep = True
    first = _service(
        gateway,
        store,
        tmp_path,
        now=clock.now,
        clock=clock.monotonic,
        sleeper=clock.sleep,
        quota_wait_policy=_quota_policy(),
    )
    with pytest.raises(KeyboardInterrupt):
        first.upload(plan, state)

    clock.advance(901.0)
    clock.sleeps.clear()
    resumed = _service(
        gateway,
        store,
        tmp_path,
        now=clock.now,
        clock=clock.monotonic,
        sleeper=clock.sleep,
        quota_wait_policy=_quota_policy(),
    ).upload(plan, store.load_state(plan.run_id))

    assert clock.sleeps == []
    assert gateway.submitted_lengths[:2] == [50, 1]
    assert resumed.frames[0].status is FrameUploadStatus.COMPLETED


def test_maximum_automatic_quota_wait_stops_safely(tmp_path: Path) -> None:
    plan, state, store = _initialized_run(tmp_path, frame_count=1)
    gateway = AutomaticQuotaGateway(quota_failures=100)
    clock = FakeUploadClock()
    policy = QuotaWaitPolicy(
        cooldown_seconds=(60.0,),
        jitter_seconds=0.0,
        max_auto_wait_seconds=100.0,
        conservative_recovery_interval_seconds=1.5,
    )
    service = _service(
        gateway,
        store,
        tmp_path,
        now=clock.now,
        clock=clock.monotonic,
        sleeper=clock.sleep,
        quota_wait_policy=policy,
    )

    with pytest.raises(CalendarUsageQuotaPause) as raised:
        service.upload(plan, state)

    saved = store.load_state(plan.run_id)
    assert raised.value.automatic_wait_exhausted is True
    assert clock.sleeps == [60.0, 40.0]
    assert gateway.create_event_calls == 3
    assert saved.frames[0].status is FrameUploadStatus.PARTIAL
    assert saved.quota_wait is not None
    assert saved.quota_wait.exhausted is True
    assert saved.quota_wait_total_seconds == 100.0
    assert saved.quota_wait_attempts == 2
    assert gateway.delete_event_calls == 0


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
