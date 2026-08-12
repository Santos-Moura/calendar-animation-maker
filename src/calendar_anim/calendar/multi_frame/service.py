from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta
from time import perf_counter, sleep
from typing import Final

from calendar_anim.calendar.event_identity import deterministic_event_id
from calendar_anim.calendar.frame_mapping.models import SingleFrameCalendarPlan
from calendar_anim.calendar.frame_mapping.service import (
    ABSOLUTE_SINGLE_FRAME_MAX_EVENTS,
    single_frame_metadata,
)
from calendar_anim.calendar.gateway import CalendarGateway
from calendar_anim.calendar.high_detail import (
    high_detail_max_events_for_run,
    is_high_detail_geometry,
)
from calendar_anim.calendar.lab import LabCalendarService
from calendar_anim.calendar.models import (
    CalendarEventDraft,
    CalendarWriteFailure,
    CalendarWriteResult,
)
from calendar_anim.calendar.multi_frame.artifacts import AnimationRunStore
from calendar_anim.calendar.multi_frame.models import (
    AnimationUploadState,
    FrameUploadExecutionResult,
    FrameUploadPlan,
    FrameUploadState,
    FrameUploadStatus,
    MultiFramePlan,
    QuotaWaitState,
    UploadPauseMetadata,
    UploadPauseReason,
)
from calendar_anim.calendar.multi_frame.performance import (
    FrameUploadPerformance,
    InvocationStatus,
    UploadInvocationPerformance,
    UploadPerformanceReport,
    begin_upload_invocation,
    finish_upload_invocation,
    initial_performance_report,
    record_frame_performance,
    refresh_performance_report,
)
from calendar_anim.calendar.multi_frame.quota_wait import QuotaWaitPolicy
from calendar_anim.calendar.multi_frame.retry import (
    CALENDAR_USAGE_QUOTA_REASON,
    DEFAULT_UPLOAD_RETRY_POLICY,
    RETRYABLE_FORBIDDEN_REASONS,
    Jitter,
    UploadRetryPolicy,
    default_jitter,
    is_calendar_usage_quota_exception,
    is_rate_limit_exception,
    is_retryable_exception,
    rate_limit_retry_delay,
    retry_delay,
)
from calendar_anim.exceptions import CalendarAnimError

DEFAULT_UPLOAD_CHUNK_SIZE: Final = 50
ProgressCallback = Callable[[int, int, int], None]
FrameCompleteCallback = Callable[[FrameUploadPerformance], None]
Clock = Callable[[], float]
Now = Callable[[], datetime]
Sleeper = Callable[[float], None]
QuotaWaitCallback = Callable[[QuotaWaitState, float], None]


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _is_rate_limit_failure(failure: CalendarWriteFailure) -> bool:
    return failure.status_code == 429 or failure.reason in RETRYABLE_FORBIDDEN_REASONS


class CalendarUsageQuotaPause(CalendarAnimError):
    """Expected circuit-breaker stop after Google Calendar usage quota exhaustion."""

    def __init__(
        self, pause: UploadPauseMetadata, *, automatic_wait_exhausted: bool = False
    ) -> None:
        self.pause = pause
        self.automatic_wait_exhausted = automatic_wait_exhausted
        super().__init__("Google Calendar usage quota exceeded")


def normalize_legacy_calendar_usage_quota_pause(
    state: AnimationUploadState,
) -> bool:
    """Upgrade a pre-circuit-breaker quota failure without losing created IDs."""

    for frame in state.frames:
        if frame.status is not FrameUploadStatus.FAILED:
            continue
        if not any(
            CALENDAR_USAGE_QUOTA_REASON in error or "Calendar usage limits exceeded" in error
            for error in frame.errors
        ):
            continue
        pause = UploadPauseMetadata(
            reason=UploadPauseReason.CALENDAR_USAGE_QUOTA_EXCEEDED,
            http_status=403,
            google_reason=CALENDAR_USAGE_QUOTA_REASON,
            frame_index=frame.frame_index,
            timestamp=state.updated_at,
            created_before_pause=frame.created_events,
            planned_events=frame.planned_events,
        )
        frame.status = FrameUploadStatus.PARTIAL
        frame.failed_events = 0
        frame.last_failure_retryable = None
        frame.quota_exceeded_count += 1
        frame.quota_circuit_breaker_count += 1
        state.pause = pause
        if not any(
            existing.frame_index == pause.frame_index and existing.timestamp == pause.timestamp
            for existing in state.pause_history
        ):
            state.pause_history.append(pause)
        return True
    return False


class MultiFrameUploadService:
    def __init__(
        self,
        gateway: CalendarGateway,
        lab: LabCalendarService,
        store: AnimationRunStore,
        *,
        chunk_size: int = DEFAULT_UPLOAD_CHUNK_SIZE,
        progress: ProgressCallback | None = None,
        frame_complete: FrameCompleteCallback | None = None,
        clock: Clock = perf_counter,
        now: Now = _utc_now,
        retry_policy: UploadRetryPolicy = DEFAULT_UPLOAD_RETRY_POLICY,
        sleeper: Sleeper = sleep,
        jitter: Jitter = default_jitter,
        quota_wait_policy: QuotaWaitPolicy | None = None,
        quota_wait_callback: QuotaWaitCallback | None = None,
    ) -> None:
        if chunk_size <= 0:
            raise ValueError("chunk size must be positive")
        self.gateway = gateway
        self.lab = lab
        self.store = store
        self.chunk_size = chunk_size
        self.progress = progress
        self.frame_complete = frame_complete
        self.clock = clock
        self.now = now
        self.retry_policy = retry_policy
        self.sleeper = sleeper
        self.jitter = jitter
        self.quota_wait_policy = quota_wait_policy
        self.quota_wait_callback = quota_wait_callback
        self._performance_report: UploadPerformanceReport | None = None
        self._current_invocation: UploadInvocationPerformance | None = None

    def upload(
        self,
        plan: MultiFramePlan,
        state: AnimationUploadState,
        *,
        recover_partial: bool = False,
    ) -> AnimationUploadState:
        normalized_legacy_pause = normalize_legacy_calendar_usage_quota_pause(state)
        self._validate(plan, state)
        self._restore_write_pacing(state)
        if state.quota_wait is not None and state.quota_wait.exhausted:
            now = self.now()
            state.quota_wait.exhausted = False
            state.quota_wait.max_wait_until = now + timedelta(
                seconds=self._quota_policy().max_auto_wait_seconds
            )
            state.quota_wait.next_retry_at = now
            state.quota_wait.last_accounted_at = now
        if state.pause is not None and state.quota_wait is None and self.quota_wait_policy:
            self._start_quota_wait(state, state.pause)
        if normalized_legacy_pause:
            self._checkpoint(plan, state)
        interrupted = [
            frame for frame in state.frames if frame.status is FrameUploadStatus.UPLOADING
        ]
        for frame in interrupted:
            frame.status = FrameUploadStatus.PARTIAL
            frame.errors.append("Previous upload ended while this frame was uploading")
        if interrupted:
            self._checkpoint(plan, state)
        performance = (
            self.store.load_performance(plan.run_id)
            if self.store.performance_json_path(plan.run_id).exists()
            else initial_performance_report(plan, state)
        )
        invocation = begin_upload_invocation(performance, state, self.now())
        invocation_started = self.clock()
        invocation_quota_wait_started = state.quota_wait_total_seconds
        self._performance_report = performance
        self._current_invocation = invocation
        self.store.save_performance(performance)
        invocation_status: InvocationStatus = "stopped"
        try:
            uploaded = self._upload_invocation(plan, state, recover_partial=recover_partial)
            if all(frame.status is FrameUploadStatus.COMPLETED for frame in uploaded.frames):
                invocation_status = "completed"
            return uploaded
        except KeyboardInterrupt:
            invocation_status = "interrupted"
            raise
        except CalendarUsageQuotaPause:
            invocation_status = "stopped"
            raise
        except Exception:
            invocation_status = "failed"
            raise
        finally:
            finish_upload_invocation(
                performance,
                plan,
                state,
                invocation,
                finished_at=self.now(),
                elapsed_seconds=max(0.0, self.clock() - invocation_started),
                quota_wait_elapsed_seconds=max(
                    0.0,
                    state.quota_wait_total_seconds - invocation_quota_wait_started,
                ),
                status=invocation_status,
            )
            self.store.save_performance(performance)
            self._performance_report = None
            self._current_invocation = None

    def _upload_invocation(
        self,
        plan: MultiFramePlan,
        state: AnimationUploadState,
        *,
        recover_partial: bool,
    ) -> AnimationUploadState:

        if state.calendar_profile != plan.calendar_profile:
            raise CalendarAnimError("Animation state refers to a different Calendar profile")

        calendar, calendar_created = self.lab.resolve(plan.calendar_name, plan.timezone)
        if state.calendar_id is not None and state.calendar_id != calendar.id:
            raise CalendarAnimError("Animation state refers to a different Calendar")
        state.calendar_id = calendar.id
        state.calendar_created = state.calendar_created or calendar_created
        if state.quota_wait is None:
            state.pause = None
        self._checkpoint(plan, state)

        for frame_summary in plan.frames:
            frame_state = state.frame(frame_summary.frame_index)
            if frame_state.status is FrameUploadStatus.COMPLETED:
                continue
            frame_plan = self.store.load_frame_plan(plan, frame_summary.frame_index)
            self._validate_frame_plan(plan, frame_summary, frame_plan)
            if (
                state.quota_wait is not None
                and state.quota_wait.frame_index == frame_state.frame_index
            ):
                self._wait_for_quota_recovery(
                    plan,
                    state,
                    frame_plan,
                    frame_state,
                    calendar.id,
                )
            self._reconcile_frame(plan, state, frame_plan, frame_state, calendar.id)
            recovery_cycle = 0
            while True:
                try:
                    completed = self._upload_frame(
                        plan, state, frame_plan, frame_state, calendar.id
                    )
                except CalendarUsageQuotaPause as error:
                    if self.quota_wait_policy is None:
                        raise
                    if state.quota_wait is None:
                        self._start_quota_wait(state, error.pause)
                    self._wait_for_quota_recovery(
                        plan,
                        state,
                        frame_plan,
                        frame_state,
                        calendar.id,
                    )
                    self._reconcile_frame(plan, state, frame_plan, frame_state, calendar.id)
                    continue
                if completed:
                    break
                if (
                    not frame_state.last_failure_retryable
                    or recovery_cycle >= self.retry_policy.max_frame_recovery_cycles
                ):
                    frame_state.status = FrameUploadStatus.FAILED
                    self._checkpoint(plan, state)
                    raise CalendarAnimError(
                        f"Frame {frame_plan.frame_index} failed after "
                        f"{frame_state.event_retry_count} event retries and "
                        f"{recovery_cycle} recovery cycle(s)"
                    )
                recovery_cycle += 1
                frame_state.recovery_cycles += 1
                self._checkpoint(plan, state)
                cooldown = retry_delay(self.retry_policy, recovery_cycle, self.jitter)
                self.sleeper(cooldown)
                frame_state.duration_seconds = (frame_state.duration_seconds or 0.0) + cooldown
                self._reconcile_frame(plan, state, frame_plan, frame_state, calendar.id)
        return state

    def _start_quota_wait(
        self,
        state: AnimationUploadState,
        pause: UploadPauseMetadata,
    ) -> None:
        policy = self._quota_policy()
        entered_at = pause.timestamp
        if entered_at + timedelta(seconds=policy.max_auto_wait_seconds) <= self.now():
            entered_at = self.now()
        max_wait_until = entered_at + timedelta(seconds=policy.max_auto_wait_seconds)
        cooldown = policy.cooldown_for_stage(0) + self.jitter(policy.jitter_seconds)
        next_retry_at = min(entered_at + timedelta(seconds=cooldown), max_wait_until)
        state.quota_wait = QuotaWaitState(
            frame_index=pause.frame_index,
            entered_at=entered_at,
            last_accounted_at=entered_at,
            next_retry_at=next_retry_at,
            max_wait_until=max_wait_until,
            stage_index=0,
            last_cooldown_seconds=cooldown,
        )
        state.quota_wait_entries += 1
        state.largest_quota_cooldown_seconds = max(state.largest_quota_cooldown_seconds, cooldown)
        state.pause = pause

    def _wait_for_quota_recovery(
        self,
        plan: MultiFramePlan,
        state: AnimationUploadState,
        frame_plan: SingleFrameCalendarPlan,
        frame_state: FrameUploadState,
        calendar_id: str,
    ) -> None:
        policy = self._quota_policy()
        while state.quota_wait is not None:
            wait = state.quota_wait
            now = self.now()
            self._account_quota_wait(state, now)
            remaining = max(0.0, (wait.next_retry_at - now).total_seconds())
            self._checkpoint(plan, state)
            if self.quota_wait_callback is not None:
                self.quota_wait_callback(wait, remaining)
            if remaining:
                self.sleeper(remaining)
            now = self.now()
            self._account_quota_wait(state, now)

            known_ids = set(frame_state.created_event_ids)
            probe = next(
                (
                    event
                    for event in frame_plan.events
                    if deterministic_event_id(event) not in known_ids
                ),
                None,
            )
            if probe is None:
                self._complete_quota_recovery(plan, state, policy)
                return

            result, retries, retryable = self._create_events_with_retry(calendar_id, [probe])
            frame_state.event_retry_count += retries
            frame_state.rate_limit_exceeded_count += result.rate_limit_exceeded_count
            frame_state.quota_exceeded_count += result.quota_exceeded_count
            frame_state.adaptive_rate_limit_cooldowns += result.adaptive_rate_limit_cooldowns
            frame_state.quota_circuit_breaker_count += result.quota_circuit_breaker_count
            for event_id in result.created_event_ids:
                if event_id not in known_ids:
                    frame_state.created_event_ids.append(event_id)
                    known_ids.add(event_id)
            frame_state.created_events = len(frame_state.created_event_ids)
            frame_state.errors.extend(result.errors)
            frame_state.last_failure_retryable = retryable if result.failed_events else None
            wait.attempts += 1
            state.quota_wait_attempts += 1

            if result.quota_exceeded_count:
                pause = self._quota_pause(frame_state, frame_plan.frame_index)
                state.pause = pause
                state.pause_history.append(pause)
                frame_state.status = FrameUploadStatus.PARTIAL
                frame_state.failed_events = 0
                frame_state.last_failure_retryable = None
                if now >= wait.max_wait_until:
                    wait.exhausted = True
                    wait.next_retry_at = now
                    self._checkpoint(plan, state)
                    raise CalendarUsageQuotaPause(
                        pause,
                        automatic_wait_exhausted=True,
                    )
                self._schedule_next_quota_probe(state, now, policy)
                self._checkpoint(plan, state)
                continue

            if result.failed_events or result.created_events != 1:
                frame_state.status = FrameUploadStatus.PARTIAL
                frame_state.failed_events = result.failed_events
                self._checkpoint(plan, state)
                raise CalendarAnimError("Calendar quota recovery probe failed")

            frame_state.failed_events = 0
            frame_state.status = FrameUploadStatus.PARTIAL
            self._complete_quota_recovery(plan, state, policy)
            self._notify(
                frame_plan.frame_index,
                frame_state.created_events,
                frame_plan.event_count,
            )
            return

    def _schedule_next_quota_probe(
        self,
        state: AnimationUploadState,
        now: datetime,
        policy: QuotaWaitPolicy,
    ) -> None:
        wait = state.quota_wait
        assert wait is not None
        wait.stage_index += 1
        cooldown = policy.cooldown_for_stage(wait.stage_index) + self.jitter(policy.jitter_seconds)
        wait.last_cooldown_seconds = cooldown
        wait.last_accounted_at = now
        wait.next_retry_at = min(
            now + timedelta(seconds=cooldown),
            wait.max_wait_until,
        )
        state.largest_quota_cooldown_seconds = max(
            state.largest_quota_cooldown_seconds,
            cooldown,
        )

    @staticmethod
    def _account_quota_wait(state: AnimationUploadState, now: datetime) -> None:
        wait = state.quota_wait
        if wait is None:
            return
        accounted_until = min(now, wait.next_retry_at)
        elapsed = max(0.0, (accounted_until - wait.last_accounted_at).total_seconds())
        state.quota_wait_total_seconds += elapsed
        wait.last_accounted_at = max(wait.last_accounted_at, accounted_until)

    def _complete_quota_recovery(
        self,
        plan: MultiFramePlan,
        state: AnimationUploadState,
        policy: QuotaWaitPolicy,
    ) -> None:
        state.quota_recoveries += 1
        state.quota_wait = None
        state.pause = None
        self._apply_conservative_recovery_pacing(state, policy)
        self._checkpoint(plan, state)

    def _quota_pause(
        self,
        frame_state: FrameUploadState,
        frame_index: int,
    ) -> UploadPauseMetadata:
        return UploadPauseMetadata(
            reason=UploadPauseReason.CALENDAR_USAGE_QUOTA_EXCEEDED,
            http_status=403,
            google_reason=CALENDAR_USAGE_QUOTA_REASON,
            frame_index=frame_index,
            timestamp=self.now(),
            created_before_pause=frame_state.created_events,
            planned_events=frame_state.planned_events,
        )

    def _quota_policy(self) -> QuotaWaitPolicy:
        if self.quota_wait_policy is None:
            raise CalendarAnimError("Automatic Calendar quota recovery is not enabled")
        return self.quota_wait_policy

    def _upload_frame(
        self,
        animation_plan: MultiFramePlan,
        state: AnimationUploadState,
        frame_plan: SingleFrameCalendarPlan,
        frame_state: FrameUploadState,
        calendar_id: str,
    ) -> bool:
        started = self.now()
        started_counter = self.clock()
        frame_state.status = FrameUploadStatus.UPLOADING
        frame_state.created_events = len(frame_state.created_event_ids)
        frame_state.failed_events = 0
        if frame_state.frame_started_at is None:
            frame_state.frame_started_at = started
        frame_state.frame_completed_at = None
        self._checkpoint(animation_plan, state)
        self._notify(frame_plan.frame_index, frame_state.created_events, frame_plan.event_count)
        known_ids = set(frame_state.created_event_ids)
        pending_events = [
            event for event in frame_plan.events if deterministic_event_id(event) not in known_ids
        ]
        try:
            for events in _chunks(pending_events, self.chunk_size):
                result, retries, retryable = self._create_events_with_retry(calendar_id, events)
                frame_state.event_retry_count += retries
                frame_state.rate_limit_exceeded_count += result.rate_limit_exceeded_count
                frame_state.quota_exceeded_count += result.quota_exceeded_count
                frame_state.adaptive_rate_limit_cooldowns += result.adaptive_rate_limit_cooldowns
                frame_state.quota_circuit_breaker_count += result.quota_circuit_breaker_count
                for event_id in result.created_event_ids:
                    if event_id not in known_ids:
                        frame_state.created_event_ids.append(event_id)
                        known_ids.add(event_id)
                frame_state.created_events = len(frame_state.created_event_ids)
                frame_state.failed_events = result.failed_events
                frame_state.errors.extend(result.errors)
                frame_state.last_failure_retryable = retryable if result.failed_events else None
                self._checkpoint(animation_plan, state)
                self._notify(
                    frame_plan.frame_index,
                    frame_state.created_events,
                    frame_plan.event_count,
                )
                if result.quota_exceeded_count:
                    pause = self._quota_pause(frame_state, frame_plan.frame_index)
                    state.pause = pause
                    state.pause_history.append(pause)
                    frame_state.status = FrameUploadStatus.PARTIAL
                    frame_state.failed_events = 0
                    frame_state.last_failure_retryable = None
                    self._checkpoint(animation_plan, state)
                    self._finish_frame(
                        animation_plan,
                        state,
                        frame_plan,
                        frame_state,
                        started_counter,
                    )
                    raise CalendarUsageQuotaPause(pause)
                if result.failed_events or result.created_events != len(events):
                    frame_state.status = (
                        FrameUploadStatus.PARTIAL
                        if frame_state.created_events
                        else FrameUploadStatus.FAILED
                    )
                    self._finish_frame(
                        animation_plan,
                        state,
                        frame_plan,
                        frame_state,
                        started_counter,
                    )
                    return False
        except CalendarUsageQuotaPause:
            raise
        except KeyboardInterrupt:
            frame_state.status = FrameUploadStatus.PARTIAL
            frame_state.errors.append("Upload interrupted by user")
            self._finish_frame(
                animation_plan,
                state,
                frame_plan,
                frame_state,
                started_counter,
            )
            raise
        except Exception as error:
            frame_state.status = (
                FrameUploadStatus.PARTIAL
                if frame_state.created_events
                else FrameUploadStatus.FAILED
            )
            frame_state.errors.append(str(error))
            self._finish_frame(
                animation_plan,
                state,
                frame_plan,
                frame_state,
                started_counter,
            )
            raise
        frame_state.status = FrameUploadStatus.COMPLETED
        frame_state.failed_events = 0
        frame_state.last_failure_retryable = None
        self._finish_frame(
            animation_plan,
            state,
            frame_plan,
            frame_state,
            started_counter,
        )
        return True

    def _finish_frame(
        self,
        animation_plan: MultiFramePlan,
        state: AnimationUploadState,
        frame_plan: SingleFrameCalendarPlan,
        frame_state: FrameUploadState,
        started_counter: float,
    ) -> None:
        completed = self.now()
        frame_state.frame_completed_at = completed
        attempt_elapsed = max(0.0, self.clock() - started_counter)
        frame_state.duration_seconds = (frame_state.duration_seconds or 0.0) + attempt_elapsed
        self.store.save_frame_result(
            animation_plan,
            FrameUploadExecutionResult(
                executed=True,
                run_id=frame_plan.run_id,
                animation_id=animation_plan.animation_id,
                frame_index=frame_plan.frame_index,
                status=frame_state.status,
                calendar_id=state.calendar_id,
                planned_events=frame_state.planned_events,
                created_events=frame_state.created_events,
                failed_events=frame_state.failed_events,
                created_event_ids=frame_state.created_event_ids,
                errors=frame_state.errors,
                event_retry_count=frame_state.event_retry_count,
                recovery_cycles=frame_state.recovery_cycles,
                last_failure_retryable=frame_state.last_failure_retryable,
                rate_limit_exceeded_count=frame_state.rate_limit_exceeded_count,
                quota_exceeded_count=frame_state.quota_exceeded_count,
                adaptive_rate_limit_cooldowns=frame_state.adaptive_rate_limit_cooldowns,
                quota_circuit_breaker_count=frame_state.quota_circuit_breaker_count,
                pause=state.pause,
            ),
        )
        self._checkpoint(animation_plan, state)
        if self._performance_report is not None and self._current_invocation is not None:
            frame_performance = record_frame_performance(
                self._current_invocation,
                animation_plan,
                frame_state,
                attempt_elapsed_seconds=attempt_elapsed,
            )
            refresh_performance_report(self._performance_report, animation_plan, state)
            self.store.save_performance(self._performance_report)
            if self.frame_complete is not None:
                self.frame_complete(frame_performance)

    def _recover_partial(
        self,
        animation_plan: MultiFramePlan,
        state: AnimationUploadState,
        frame_plan: SingleFrameCalendarPlan,
        frame_state: FrameUploadState,
        calendar_id: str,
    ) -> None:
        existing = self.gateway.find_events_by_private_metadata(
            calendar_id, single_frame_metadata(frame_plan)
        )
        deletion = self.gateway.delete_events(calendar_id, [event.id for event in existing])
        if deletion.failed_events:
            frame_state.errors.extend(deletion.errors)
            self._checkpoint(animation_plan, state)
            raise CalendarAnimError(
                f"Unable to recover frame {frame_plan.frame_index}: "
                f"{deletion.failed_events} deletion(s) failed"
            )
        frame_state.status = FrameUploadStatus.PENDING
        frame_state.created_events = 0
        frame_state.failed_events = 0
        frame_state.errors = []
        frame_state.created_event_ids = []
        frame_state.frame_started_at = None
        frame_state.frame_completed_at = None
        frame_state.duration_seconds = None
        self._checkpoint(animation_plan, state)

    def _reconcile_frame(
        self,
        animation_plan: MultiFramePlan,
        state: AnimationUploadState,
        frame_plan: SingleFrameCalendarPlan,
        frame_state: FrameUploadState,
        calendar_id: str,
    ) -> None:
        existing = self.gateway.find_events_by_private_metadata(
            calendar_id, single_frame_metadata(frame_plan)
        )
        expected_ids = {deterministic_event_id(event) for event in frame_plan.events}
        remote_ids = {event.id for event in existing}
        unknown = remote_ids - expected_ids
        if unknown:
            self._recover_partial(animation_plan, state, frame_plan, frame_state, calendar_id)
            return
        frame_state.created_event_ids = sorted(remote_ids)
        frame_state.created_events = len(remote_ids)
        frame_state.failed_events = 0
        if remote_ids == expected_ids:
            frame_state.status = FrameUploadStatus.COMPLETED
            frame_state.frame_completed_at = self.now()
        else:
            frame_state.status = FrameUploadStatus.PENDING
        self._checkpoint(animation_plan, state)

    def _create_events_with_retry(
        self, calendar_id: str, events: Sequence[CalendarEventDraft]
    ) -> tuple[CalendarWriteResult, int, bool | None]:
        pending = list(events)
        created_ids: list[str] = []
        retries = 0
        final_errors: list[str] = []
        rate_limit_exceeded_count = 0
        quota_exceeded_count = 0
        adaptive_rate_limit_cooldowns = 0
        quota_circuit_breaker_count = 0
        for attempt in range(1, self.retry_policy.max_event_attempts + 1):
            try:
                result = self.gateway.create_events(calendar_id, pending)
            except Exception as error:
                if is_calendar_usage_quota_exception(error):
                    return (
                        CalendarWriteResult(
                            created_event_ids=created_ids,
                            failed_events=len(pending),
                            errors=[str(error)],
                            quota_exceeded_count=1,
                            quota_circuit_breaker_count=1,
                        ),
                        retries,
                        None,
                    )
                retryable = is_retryable_exception(error)
                rate_limited = is_rate_limit_exception(error)
                if rate_limited:
                    rate_limit_exceeded_count += 1
                final_errors = [str(error)]
                if not retryable or attempt == self.retry_policy.max_event_attempts:
                    return (
                        CalendarWriteResult(
                            created_event_ids=created_ids,
                            failed_events=len(pending),
                            errors=final_errors,
                            rate_limit_exceeded_count=rate_limit_exceeded_count,
                            adaptive_rate_limit_cooldowns=adaptive_rate_limit_cooldowns,
                        ),
                        retries,
                        retryable,
                    )
                retries += 1
                delay = (
                    rate_limit_retry_delay(self.retry_policy, attempt, self.jitter)
                    if rate_limited
                    else retry_delay(self.retry_policy, attempt, self.jitter)
                )
                if rate_limited:
                    adaptive_rate_limit_cooldowns += 1
                self.sleeper(delay)
                continue

            rate_limit_exceeded_count += result.rate_limit_exceeded_count
            quota_exceeded_count += result.quota_exceeded_count
            quota_circuit_breaker_count += result.quota_circuit_breaker_count
            created_indexes = set(result.created_event_indexes)
            created_ids.extend(result.created_event_ids)
            missing_indexes = [
                index for index in range(len(pending)) if index not in created_indexes
            ]
            if not missing_indexes:
                return (
                    CalendarWriteResult(
                        created_event_ids=created_ids,
                        created_event_indexes=list(range(len(events))),
                        rate_limit_exceeded_count=rate_limit_exceeded_count,
                        quota_exceeded_count=quota_exceeded_count,
                        adaptive_rate_limit_cooldowns=adaptive_rate_limit_cooldowns,
                        quota_circuit_breaker_count=quota_circuit_breaker_count,
                    ),
                    retries,
                    None,
                )
            if result.quota_exceeded_count:
                return (
                    CalendarWriteResult(
                        created_event_ids=created_ids,
                        failed_events=len(missing_indexes),
                        errors=result.errors,
                        failures=result.failures,
                        rate_limit_exceeded_count=rate_limit_exceeded_count,
                        quota_exceeded_count=quota_exceeded_count,
                        adaptive_rate_limit_cooldowns=adaptive_rate_limit_cooldowns,
                        quota_circuit_breaker_count=quota_circuit_breaker_count,
                    ),
                    retries,
                    None,
                )
            failures = {failure.event_index: failure for failure in result.failures}
            retryable = bool(failures) and all(
                failures.get(index) is not None and failures[index].retryable
                for index in missing_indexes
            )
            final_errors = result.errors or [
                f"Gateway did not persist {len(missing_indexes)} event(s)"
            ]
            if not retryable or attempt == self.retry_policy.max_event_attempts:
                return (
                    CalendarWriteResult(
                        created_event_ids=created_ids,
                        failed_events=len(missing_indexes),
                        errors=final_errors,
                        failures=[
                            failures[index] for index in missing_indexes if index in failures
                        ],
                        rate_limit_exceeded_count=rate_limit_exceeded_count,
                        quota_exceeded_count=quota_exceeded_count,
                        adaptive_rate_limit_cooldowns=adaptive_rate_limit_cooldowns,
                        quota_circuit_breaker_count=quota_circuit_breaker_count,
                    ),
                    retries,
                    retryable,
                )
            pending = [pending[index] for index in missing_indexes]
            retries += 1
            delay = (
                rate_limit_retry_delay(self.retry_policy, attempt, self.jitter)
                if any(_is_rate_limit_failure(failures[index]) for index in missing_indexes)
                else retry_delay(self.retry_policy, attempt, self.jitter)
            )
            if any(_is_rate_limit_failure(failures[index]) for index in missing_indexes):
                adaptive_rate_limit_cooldowns += 1
            self.sleeper(delay)
        raise AssertionError("unreachable retry loop")

    def _checkpoint(self, plan: MultiFramePlan, state: AnimationUploadState) -> None:
        self._capture_write_pacing(state)
        self.store.save_state(state)
        self.store.save_report(plan, state)

    def _capture_write_pacing(self, state: AnimationUploadState) -> None:
        snapshotter = getattr(self.gateway, "write_pacing_snapshot", None)
        if callable(snapshotter):
            state.write_pacing = snapshotter()

    def _restore_write_pacing(self, state: AnimationUploadState) -> None:
        if state.write_pacing is None:
            return
        restore = getattr(self.gateway, "restore_write_pacing", None)
        if callable(restore):
            restore(state.write_pacing)

    def _apply_conservative_recovery_pacing(
        self,
        state: AnimationUploadState,
        policy: QuotaWaitPolicy,
    ) -> None:
        snapshotter = getattr(self.gateway, "write_pacing_snapshot", None)
        restore = getattr(self.gateway, "restore_write_pacing", None)
        if not callable(snapshotter) or not callable(restore):
            return
        snapshot = snapshotter()
        data = snapshot.model_copy(
            update={
                "previous_interval_seconds": snapshot.current_interval_seconds,
                "current_interval_seconds": max(
                    snapshot.current_interval_seconds,
                    policy.conservative_recovery_interval_seconds,
                ),
                "successful_writes_since_rate_limit": 0,
            }
        )
        restore(data)
        state.write_pacing = data

    def _notify(self, frame_index: int, created: int, planned: int) -> None:
        if self.progress is not None:
            self.progress(frame_index, created, planned)

    @staticmethod
    def _validate(plan: MultiFramePlan, state: AnimationUploadState) -> None:
        if not plan.profile_ready:
            raise CalendarAnimError("Calibration profile is NOT READY; animation upload is blocked")
        allowed_max_events = (
            high_detail_max_events_for_run(plan.run_id)
            if is_high_detail_geometry(
                plan.grid_profile,
                plan.target_grid_width,
                plan.target_grid_height,
                plan.slots_per_day,
                plan.vertical_step_minutes,
                plan.visible_start_hour,
                plan.visible_end_hour,
            )
            else ABSOLUTE_SINGLE_FRAME_MAX_EVENTS
        )
        if plan.max_events_per_frame > allowed_max_events:
            raise CalendarAnimError(
                f"Per-frame limit exceeds the absolute safety limit of {allowed_max_events}"
            )
        expected = [(frame.frame_index, frame.planned_events) for frame in plan.frames]
        actual = [(frame.frame_index, frame.planned_events) for frame in state.frames]
        if (
            state.run_id != plan.run_id
            or state.animation_id != plan.animation_id
            or actual != expected
        ):
            raise CalendarAnimError("Animation state does not match its plan")

    @staticmethod
    def _validate_frame_plan(
        animation_plan: MultiFramePlan,
        frame_summary: FrameUploadPlan,
        frame_plan: SingleFrameCalendarPlan,
    ) -> None:
        if frame_plan.animation_id != animation_plan.animation_id:
            raise CalendarAnimError("Frame plan animation ID does not match animation plan")
        if frame_plan.frame_index != frame_summary.frame_index:
            raise CalendarAnimError("Frame index does not match animation plan")
        if frame_plan.run_id != frame_summary.frame_run_id:
            raise CalendarAnimError("Frame run ID does not match animation plan")
        if frame_plan.week_start_date != frame_summary.week_start:
            raise CalendarAnimError("Frame week does not match animation plan")
        if frame_plan.mapping_mode is not animation_plan.mapping_mode:
            raise CalendarAnimError("Frame mapping mode does not match animation plan")
        if frame_plan.subcolumn_order_strategy is not animation_plan.subcolumn_order_strategy:
            raise CalendarAnimError("Frame ordering strategy does not match animation plan")
        if frame_plan.event_count != frame_summary.planned_events:
            raise CalendarAnimError("Frame plan event count does not match animation plan")
        if frame_plan.event_count > animation_plan.max_events_per_frame:
            raise CalendarAnimError(
                f"Frame {frame_plan.frame_index} exceeds the per-frame safety limit"
            )
        if not frame_plan.profile_ready:
            raise CalendarAnimError(f"Frame {frame_plan.frame_index} mapper is NOT READY")


def _chunks(
    events: Sequence[CalendarEventDraft], chunk_size: int
) -> list[Sequence[CalendarEventDraft]]:
    return [events[index : index + chunk_size] for index in range(0, len(events), chunk_size)]
