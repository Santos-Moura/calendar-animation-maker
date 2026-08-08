from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from typing import Final

from calendar_anim.calendar.frame_mapping.models import SingleFrameCalendarPlan
from calendar_anim.calendar.frame_mapping.service import (
    ABSOLUTE_SINGLE_FRAME_MAX_EVENTS,
    single_frame_metadata,
)
from calendar_anim.calendar.gateway import CalendarGateway
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
from calendar_anim.exceptions import CalendarAnimError

DEFAULT_UPLOAD_CHUNK_SIZE: Final = 50
ProgressCallback = Callable[[int, int, int], None]


class MultiFrameUploadService:
    def __init__(
        self,
        gateway: CalendarGateway,
        lab: LabCalendarService,
        store: AnimationRunStore,
        *,
        chunk_size: int = DEFAULT_UPLOAD_CHUNK_SIZE,
        progress: ProgressCallback | None = None,
    ) -> None:
        if chunk_size <= 0:
            raise ValueError("chunk size must be positive")
        self.gateway = gateway
        self.lab = lab
        self.store = store
        self.chunk_size = chunk_size
        self.progress = progress

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
        started = datetime.now(UTC)
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
                    self._finish_frame(animation_plan, state, frame_plan, frame_state, created_ids)
                    return False
        except KeyboardInterrupt:
            frame_state.status = FrameUploadStatus.PARTIAL
            frame_state.errors.append("Upload interrupted by user")
            self._finish_frame(animation_plan, state, frame_plan, frame_state, created_ids)
            raise
        except Exception as error:
            frame_state.status = (
                FrameUploadStatus.PARTIAL
                if frame_state.created_events
                else FrameUploadStatus.FAILED
            )
            frame_state.errors.append(str(error))
            self._finish_frame(animation_plan, state, frame_plan, frame_state, created_ids)
            raise
        frame_state.status = FrameUploadStatus.COMPLETED
        self._finish_frame(animation_plan, state, frame_plan, frame_state, created_ids)
        return True

    def _finish_frame(
        self,
        animation_plan: MultiFramePlan,
        state: AnimationUploadState,
        frame_plan: SingleFrameCalendarPlan,
        frame_state: FrameUploadState,
        created_ids: list[str],
    ) -> None:
        completed = datetime.now(UTC)
        frame_state.frame_completed_at = completed
        if frame_state.frame_started_at is not None:
            frame_state.duration_seconds = max(
                0.0, (completed - frame_state.frame_started_at).total_seconds()
            )
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
        if plan.max_events_per_frame > ABSOLUTE_SINGLE_FRAME_MAX_EVENTS:
            raise CalendarAnimError(
                f"Per-frame limit exceeds the absolute safety limit of "
                f"{ABSOLUTE_SINGLE_FRAME_MAX_EVENTS}"
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
