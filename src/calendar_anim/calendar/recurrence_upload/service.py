from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from random import Random
from time import monotonic, sleep

from googleapiclient.errors import HttpError

from calendar_anim.calendar.multi_frame.models import QuotaWaitState
from calendar_anim.calendar.multi_frame.quota_wait import QuotaWaitPolicy
from calendar_anim.calendar.multi_frame.retry import (
    UploadRetryPolicy,
    is_retryable_exception,
    rate_limit_retry_delay,
    retry_delay,
)
from calendar_anim.calendar.recurrence_compaction.models import (
    RecurrenceMigrationPlan,
    RecurringParentPlan,
)
from calendar_anim.calendar.recurrence_upload.artifacts import (
    RecurrenceUploadStore,
    performance_from_state,
)
from calendar_anim.calendar.recurrence_upload.gateway import (
    GoogleRecurrenceUploadGateway,
    ParentInsertError,
)
from calendar_anim.calendar.recurrence_upload.models import (
    ParentUploadState,
    ParentUploadStatus,
    RecurrenceUploadState,
)
from calendar_anim.exceptions import CalendarAnimError

ProgressCallback = Callable[[RecurrenceUploadState], None]
QuotaCallback = Callable[[QuotaWaitState, float], None]


class RecurrenceUploadService:
    def __init__(
        self,
        gateway: GoogleRecurrenceUploadGateway,
        store: RecurrenceUploadStore,
        *,
        retry_policy: UploadRetryPolicy | None = None,
        quota_policy: QuotaWaitPolicy | None = None,
        progress: ProgressCallback | None = None,
        quota_wait: QuotaCallback | None = None,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
        clock: Callable[[], float] = monotonic,
        sleeper: Callable[[float], None] = sleep,
        jitter: Callable[[float], float] | None = None,
        checkpoint_interval: int = 50,
    ) -> None:
        self.gateway = gateway
        self.store = store
        self.retry_policy = retry_policy or UploadRetryPolicy()
        self.quota_policy = quota_policy or QuotaWaitPolicy(
            cooldown_seconds=(900, 1800, 3600, 7200, 14400),
            jitter_seconds=60,
            max_auto_wait_seconds=48 * 3600,
            conservative_recovery_interval_seconds=1.5,
        )
        self.progress = progress
        self.quota_callback = quota_wait
        self.now = now
        self.clock = clock
        self.sleeper = sleeper
        self.jitter = jitter or (lambda maximum: Random().uniform(0, maximum))
        self.checkpoint_interval = checkpoint_interval
        self._active_checkpoint_started: float | None = None

    def upload(
        self,
        plan: RecurrenceMigrationPlan,
        state: RecurrenceUploadState,
        calendar_id: str,
    ) -> RecurrenceUploadState:
        if state.calendar_profile != "account-b":
            raise CalendarAnimError("Final recurrence bulk is restricted to account-b")
        if state.calendar_id not in {None, calendar_id}:
            raise CalendarAnimError("Upload checkpoint belongs to another Calendar ID")
        state.calendar_id = calendar_id
        state.started_at = state.started_at or self.now()
        self.gateway.restore_write_pacing(state.write_pacing)
        started = self.clock()
        self._active_checkpoint_started = started
        wall_start = self.now()
        try:
            for index, parent in enumerate(plan.parents):
                parent_state = state.parents[index]
                if parent_state.parent_id != parent.parent_id:
                    raise CalendarAnimError("Parent checkpoint sequence differs from plan")
                if parent_state.status is ParentUploadStatus.COMPLETED:
                    continue
                self._upload_parent(parent, parent_state, state, calendar_id, index)
                if state.completed_count % self.checkpoint_interval == 0:
                    self._checkpoint(state, wall_start)
                    if self.progress is not None:
                        self.progress(state)
            self._checkpoint(state, wall_start)
            return state
        except KeyboardInterrupt:
            self._checkpoint(state, wall_start)
            raise
        except Exception:
            self._checkpoint(state, wall_start)
            raise
        finally:
            if state.completed_count == len(state.parents):
                self._checkpoint(state, wall_start)

    def _upload_parent(
        self,
        parent: RecurringParentPlan,
        parent_state: ParentUploadState,
        state: RecurrenceUploadState,
        calendar_id: str,
        index: int,
    ) -> None:
        if parent_state.status in {
            ParentUploadStatus.UPLOADING,
            ParentUploadStatus.PARTIAL,
        } and self._reconcile(parent, parent_state, state, calendar_id):
            return
        parent_state.status = ParentUploadStatus.UPLOADING
        attempt = 0
        while True:
            attempt += 1
            parent_state.insert_calls += 1
            state.events_insert_calls += 1
            try:
                created = self.gateway.insert_parent(calendar_id, parent)
                if created != parent.parent_id:
                    raise CalendarAnimError(
                        f"Calendar returned unexpected parent ID {created} for {parent.parent_id}"
                    )
                self._complete(parent_state)
                state.write_pacing = self.gateway.write_pacing_snapshot()
                state.quota_wait = None
                return
            except ParentInsertError as error:
                parent_state.last_error = str(error)
                state.write_pacing = self.gateway.write_pacing_snapshot()
                if error.status_code == 409:
                    if self._reconcile(parent, parent_state, state, calendar_id, conflict=True):
                        return
                    raise CalendarAnimError(
                        f"409 parent {parent.parent_id} exists but differs from the plan"
                    ) from error
                if error.quota_exceeded:
                    state.quota_exceeded_count += 1
                    parent_state.status = ParentUploadStatus.PARTIAL
                    self.store.save_state(state)
                    self._wait_for_quota(parent, parent_state, state, calendar_id, index)
                    if parent_state.status is ParentUploadStatus.COMPLETED:
                        return
                    attempt = 0
                    continue
                if error.rate_limited:
                    state.rate_limit_exceeded_count += 1
                    state.last_rate_limit_timestamp = self.now()
                if error.retryable and attempt < self.retry_policy.max_event_attempts:
                    parent_state.retry_count += 1
                    state.retry_count += 1
                    self.store.save_state(state)
                    delay = (
                        rate_limit_retry_delay(self.retry_policy, attempt, self.jitter)
                        if error.rate_limited
                        else retry_delay(self.retry_policy, attempt, self.jitter)
                    )
                    self.sleeper(delay)
                    if self._reconcile(parent, parent_state, state, calendar_id):
                        return
                    continue
                parent_state.status = ParentUploadStatus.PARTIAL
                self.store.save_state(state)
                raise CalendarAnimError(
                    f"Parent {parent.parent_id} stopped safely after {attempt} attempt(s)"
                ) from error

    def _reconcile(
        self,
        parent: RecurringParentPlan,
        parent_state: ParentUploadState,
        state: RecurrenceUploadState,
        calendar_id: str,
        *,
        conflict: bool = False,
    ) -> bool:
        try:
            remote = self.gateway.get_parent(calendar_id, parent.parent_id)
        except Exception as error:
            if isinstance(error, HttpError) or is_retryable_exception(error):
                return False
            raise
        if remote is None:
            return False
        if not self.gateway.parent_matches(remote, parent):
            raise CalendarAnimError(
                f"Remote parent {parent.parent_id} differs from deterministic plan"
            )
        self._complete(parent_state)
        parent_state.reconciled = True
        if conflict:
            state.conflict_reconciliations += 1
        else:
            state.remote_reconciliations += 1
        self.store.save_state(state)
        return True

    def _wait_for_quota(
        self,
        parent: RecurringParentPlan,
        parent_state: ParentUploadState,
        state: RecurrenceUploadState,
        calendar_id: str,
        index: int,
    ) -> None:
        policy = self.quota_policy
        entered = self.now()
        if state.quota_wait is None:
            cooldown = policy.cooldown_for_stage(0) + self.jitter(policy.jitter_seconds)
            state.quota_wait = QuotaWaitState(
                frame_index=index,
                entered_at=entered,
                last_accounted_at=entered,
                next_retry_at=entered + timedelta(seconds=cooldown),
                max_wait_until=entered + timedelta(seconds=policy.max_auto_wait_seconds),
                stage_index=0,
                last_cooldown_seconds=cooldown,
            )
            state.quota_wait_entries += 1
            self.store.save_state(state)
        while state.quota_wait is not None:
            wait = state.quota_wait
            now = self.now()
            if now >= wait.max_wait_until:
                wait.exhausted = True
                self.store.save_state(state)
                raise CalendarAnimError(
                    "Maximum automatic Calendar quota wait reached; resume later with --resume"
                )
            remaining = max(0.0, (wait.next_retry_at - now).total_seconds())
            if self.quota_callback is not None:
                self.quota_callback(wait, remaining)
            if remaining:
                self._pause_active_clock(state)
                self.sleeper(remaining)
                self._active_checkpoint_started = self.clock()
                state.quota_wait_total_seconds += remaining
            wait.attempts += 1
            state.quota_wait_attempts += 1
            self.store.save_state(state)
            if self._reconcile(parent, parent_state, state, calendar_id):
                state.quota_recoveries += 1
                state.quota_wait = None
                self.gateway.configure_write_pacing(
                    policy.conservative_recovery_interval_seconds,
                    current_interval_seconds=max(
                        policy.conservative_recovery_interval_seconds,
                        state.write_pacing.current_interval_seconds,
                    ),
                )
                state.write_pacing = self.gateway.write_pacing_snapshot()
                self.store.save_state(state)
                return
            parent_state.insert_calls += 1
            state.events_insert_calls += 1
            try:
                self.gateway.insert_parent(calendar_id, parent)
            except ParentInsertError as error:
                if error.quota_exceeded:
                    state.quota_exceeded_count += 1
                    wait.stage_index += 1
                    cooldown = policy.cooldown_for_stage(wait.stage_index) + self.jitter(
                        policy.jitter_seconds
                    )
                    wait.last_cooldown_seconds = cooldown
                    wait.next_retry_at = self.now() + timedelta(seconds=cooldown)
                    wait.last_accounted_at = self.now()
                    self.store.save_state(state)
                    continue
                if error.rate_limited:
                    state.rate_limit_exceeded_count += 1
                    state.last_rate_limit_timestamp = self.now()
                if error.retryable:
                    # The real-parent quota probe may itself hit a short rate limit,
                    # temporary network failure, or a lost response. Reconcile first;
                    # otherwise return to the normal bounded retry path unattended.
                    if self._reconcile(parent, parent_state, state, calendar_id):
                        state.quota_recoveries += 1
                        state.quota_wait = None
                        self._configure_quota_recovery(state)
                        self.store.save_state(state)
                        return
                    state.quota_wait = None
                    parent_state.retry_count += 1
                    state.retry_count += 1
                    self.store.save_state(state)
                    delay = (
                        rate_limit_retry_delay(self.retry_policy, 1, self.jitter)
                        if error.rate_limited
                        else retry_delay(self.retry_policy, 1, self.jitter)
                    )
                    self.sleeper(delay)
                    return
                state.quota_wait = None
                self.store.save_state(state)
                raise
            self._complete(parent_state)
            state.quota_recoveries += 1
            state.quota_wait = None
            self._configure_quota_recovery(state)
            self.store.save_state(state)
            return

    def _configure_quota_recovery(self, state: RecurrenceUploadState) -> None:
        interval = self.quota_policy.conservative_recovery_interval_seconds
        self.gateway.configure_write_pacing(
            interval,
            current_interval_seconds=max(interval, state.write_pacing.current_interval_seconds),
        )
        state.write_pacing = self.gateway.write_pacing_snapshot()

    def _checkpoint(self, state: RecurrenceUploadState, wall_start: datetime) -> None:
        now_clock = self.clock()
        if self._active_checkpoint_started is not None:
            state.active_upload_seconds += max(0.0, now_clock - self._active_checkpoint_started)
        self._active_checkpoint_started = now_clock
        state.write_pacing = self.gateway.write_pacing_snapshot()
        self.store.save_state(state)
        started = state.started_at or wall_start
        wall = max(0.0, (self.now() - started).total_seconds())
        self.store.save_performance(performance_from_state(state, wall))

    def _pause_active_clock(self, state: RecurrenceUploadState) -> None:
        now_clock = self.clock()
        if self._active_checkpoint_started is not None:
            state.active_upload_seconds += max(0.0, now_clock - self._active_checkpoint_started)
        self._active_checkpoint_started = None

    def _complete(self, parent_state: ParentUploadState) -> None:
        parent_state.status = ParentUploadStatus.COMPLETED
        parent_state.last_error = None
        parent_state.completed_at = self.now()
