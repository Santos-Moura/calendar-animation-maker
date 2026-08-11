from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from time import perf_counter
from typing import Final

from calendar_anim.calendar.frame_mapping.models import SingleFrameCalendarPlan
from calendar_anim.calendar.frame_mapping.service import (
    ABSOLUTE_SINGLE_FRAME_MAX_EVENTS,
    single_frame_metadata,
)
from calendar_anim.calendar.gateway import CalendarGateway
from calendar_anim.calendar.high_detail import (
    HIGH_DETAIL_EXPERIMENTAL_MAX_EVENTS,
    is_high_detail_geometry,
)
from calendar_anim.calendar.lab import LabCalendarService
from calendar_anim.calendar.models import CalendarEventDraft
from calendar_anim.calendar.multi_frame.artifacts import AnimationRunStore
from calendar_anim.calendar.multi_frame.models import (
    AnimationUploadState,
    FrameUploadExecutionResult,
    FrameUploadPlan,
    FrameUploadState,
    FrameUploadStatus,
    MultiFramePlan,
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
from calendar_anim.exceptions import CalendarAnimError

DEFAULT_UPLOAD_CHUNK_SIZE: Final = 50
ProgressCallback = Callable[[int, int, int], None]
FrameCompleteCallback = Callable[[FrameUploadPerformance], None]
Clock = Callable[[], float]
Now = Callable[[], datetime]


def _utc_now() -> datetime:
    return datetime.now(UTC)


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
        self._performance_report: UploadPerformanceReport | None = None
        self._current_invocation: UploadInvocationPerformance | None = None

    def upload(
        self,
        plan: MultiFramePlan,
        state: AnimationUploadState,
        *,
        recover_partial: bool = False,
    ) -> AnimationUploadState:
        self._validate(plan, state)
        interrupted = [
            frame for frame in state.frames if frame.status is FrameUploadStatus.UPLOADING
        ]
        for frame in interrupted:
            frame.status = FrameUploadStatus.PARTIAL
            frame.errors.append("Previous upload ended while this frame was uploading")
        if interrupted:
            self._checkpoint(plan, state)
        partial = [
            frame.frame_index for frame in state.frames if frame.status is FrameUploadStatus.PARTIAL
        ]
        if partial and not recover_partial:
            indexes = ", ".join(str(index) for index in partial)
            raise CalendarAnimError(
                f"Partial frame recovery required for frame(s): {indexes}. "
                "Recovery deletes only those frames before recreating them."
            )

        performance = (
            self.store.load_performance(plan.run_id)
            if self.store.performance_json_path(plan.run_id).exists()
            else initial_performance_report(plan, state)
        )
        invocation = begin_upload_invocation(performance, state, self.now())
        invocation_started = self.clock()
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

        calendar, calendar_created = self.lab.resolve(plan.calendar_name, plan.timezone)
        if state.calendar_id is not None and state.calendar_id != calendar.id:
            raise CalendarAnimError("Animation state refers to a different Calendar")
        state.calendar_id = calendar.id
        state.calendar_created = state.calendar_created or calendar_created
        self._checkpoint(plan, state)

        for frame_summary in plan.frames:
            frame_state = state.frame(frame_summary.frame_index)
            if frame_state.status is FrameUploadStatus.COMPLETED:
                continue
            frame_plan = self.store.load_frame_plan(plan, frame_summary.frame_index)
            self._validate_frame_plan(plan, frame_summary, frame_plan)
            if frame_state.status is FrameUploadStatus.PARTIAL:
                self._recover_partial(plan, state, frame_plan, frame_state, calendar.id)
            existing = self.gateway.find_events_by_private_metadata(
                calendar.id, single_frame_metadata(frame_plan)
            )
            if existing:
                frame_state.status = FrameUploadStatus.PARTIAL
                frame_state.errors = [
                    "Remote events exist while local state is not completed; "
                    "explicit recovery is required"
                ]
                self._checkpoint(plan, state)
                raise CalendarAnimError(
                    f"Frame {frame_plan.frame_index} has {len(existing)} remote event(s) but "
                    f"local state is {frame_state.status.value}"
                )
            completed = self._upload_frame(plan, state, frame_plan, frame_state, calendar.id)
            if not completed:
                return state
        return state

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
        frame_state.created_events = 0
        frame_state.failed_events = 0
        frame_state.errors = []
        frame_state.frame_started_at = started
        frame_state.frame_completed_at = None
        frame_state.duration_seconds = None
        created_ids: list[str] = []
        self._checkpoint(animation_plan, state)
        self._notify(frame_plan.frame_index, 0, frame_plan.event_count)
        try:
            for events in _chunks(frame_plan.events, self.chunk_size):
                result = self.gateway.create_events(calendar_id, events)
                created_ids.extend(result.created_event_ids)
                frame_state.created_events += result.created_events
                frame_state.failed_events += result.failed_events
                frame_state.errors.extend(result.errors)
                self._checkpoint(animation_plan, state)
                self._notify(
                    frame_plan.frame_index,
                    frame_state.created_events,
                    frame_plan.event_count,
                )
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
                        created_ids,
                        started_counter,
                    )
                    return False
        except KeyboardInterrupt:
            frame_state.status = FrameUploadStatus.PARTIAL
            frame_state.errors.append("Upload interrupted by user")
            self._finish_frame(
                animation_plan,
                state,
                frame_plan,
                frame_state,
                created_ids,
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
                created_ids,
                started_counter,
            )
            raise
        frame_state.status = FrameUploadStatus.COMPLETED
        self._finish_frame(
            animation_plan,
            state,
            frame_plan,
            frame_state,
            created_ids,
            started_counter,
        )
        return True

    def _finish_frame(
        self,
        animation_plan: MultiFramePlan,
        state: AnimationUploadState,
        frame_plan: SingleFrameCalendarPlan,
        frame_state: FrameUploadState,
        created_ids: list[str],
        started_counter: float,
    ) -> None:
        completed = self.now()
        frame_state.frame_completed_at = completed
        frame_state.duration_seconds = max(0.0, self.clock() - started_counter)
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
                created_event_ids=created_ids,
                errors=frame_state.errors,
            ),
        )
        self._checkpoint(animation_plan, state)
        if self._performance_report is not None and self._current_invocation is not None:
            frame_performance = record_frame_performance(
                self._current_invocation, animation_plan, frame_state
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
        frame_state.frame_started_at = None
        frame_state.frame_completed_at = None
        frame_state.duration_seconds = None
        self._checkpoint(animation_plan, state)

    def _checkpoint(self, plan: MultiFramePlan, state: AnimationUploadState) -> None:
        self.store.save_state(state)
        self.store.save_report(plan, state)

    def _notify(self, frame_index: int, created: int, planned: int) -> None:
        if self.progress is not None:
            self.progress(frame_index, created, planned)

    @staticmethod
    def _validate(plan: MultiFramePlan, state: AnimationUploadState) -> None:
        if not plan.profile_ready:
            raise CalendarAnimError("Calibration profile is NOT READY; animation upload is blocked")
        allowed_max_events = (
            HIGH_DETAIL_EXPERIMENTAL_MAX_EVENTS
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
