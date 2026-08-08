from dataclasses import dataclass

from calendar_anim.calendar.frame_mapping.service import single_frame_metadata
from calendar_anim.calendar.lab import LabCalendarService
from calendar_anim.calendar.models import CalendarEventInfo, CalendarInfo
from calendar_anim.calendar.multi_frame.artifacts import AnimationRunStore
from calendar_anim.calendar.multi_frame.models import (
    AnimationCleanupResult,
    AnimationUploadState,
    FrameUploadExecutionResult,
    FrameUploadPlan,
    FrameUploadStatus,
    MultiFramePlan,
)
from calendar_anim.exceptions import CalendarAnimError


@dataclass(frozen=True)
class AnimationCleanupMatch:
    calendar: CalendarInfo | None
    events_by_frame: dict[int, list[CalendarEventInfo]]

    @property
    def event_count(self) -> int:
        return sum(len(events) for events in self.events_by_frame.values())


class MultiFrameCleanupService:
    def __init__(self, lab: LabCalendarService, store: AnimationRunStore) -> None:
        self.lab = lab
        self.gateway = lab.gateway
        self.store = store

    def find_matches(
        self, plan: MultiFramePlan, frame_index: int | None = None
    ) -> AnimationCleanupMatch:
        selected = self._selected_frames(plan, frame_index)
        calendar = self.lab.find(plan.calendar_name)
        if calendar is None:
            return AnimationCleanupMatch(
                calendar=None,
                events_by_frame={frame.frame_index: [] for frame in selected},
            )
        events_by_frame: dict[int, list[CalendarEventInfo]] = {}
        for frame in selected:
            frame_plan = self.store.load_frame_plan(plan, frame.frame_index)
            events_by_frame[frame.frame_index] = self.gateway.find_events_by_private_metadata(
                calendar.id, single_frame_metadata(frame_plan)
            )
        return AnimationCleanupMatch(calendar=calendar, events_by_frame=events_by_frame)

    def cleanup(
        self,
        plan: MultiFramePlan,
        state: AnimationUploadState,
        match: AnimationCleanupMatch,
    ) -> AnimationCleanupResult:
        selected = sorted(match.events_by_frame)
        if match.calendar is None:
            return AnimationCleanupResult(
                selected_frames=selected,
                matched_events=0,
                deleted_events=0,
                failed_events=0,
            )
        deleted = 0
        failed = 0
        errors: list[str] = []
        for frame_index in selected:
            events = match.events_by_frame[frame_index]
            result = self.gateway.delete_events(match.calendar.id, [event.id for event in events])
            deleted += result.deleted_events
            failed += result.failed_events
            errors.extend(result.errors)
            frame_state = state.frame(frame_index)
            if result.failed_events:
                frame_state.status = FrameUploadStatus.PARTIAL
                frame_state.errors.extend(result.errors)
            else:
                frame_state.status = FrameUploadStatus.PENDING
                frame_state.created_events = 0
                frame_state.failed_events = 0
                frame_state.errors = []
                frame_state.frame_started_at = None
                frame_state.frame_completed_at = None
                frame_state.duration_seconds = None
                frame_plan = self.store.load_frame_plan(plan, frame_index)
                self.store.save_frame_result(
                    plan,
                    FrameUploadExecutionResult(
                        executed=False,
                        run_id=frame_plan.run_id,
                        animation_id=plan.animation_id,
                        frame_index=frame_index,
                        status=FrameUploadStatus.PENDING,
                        planned_events=frame_plan.event_count,
                    ),
                )
            self.store.save_state(state)
            self.store.save_report(plan, state)
        return AnimationCleanupResult(
            selected_frames=selected,
            matched_events=match.event_count,
            deleted_events=deleted,
            failed_events=failed,
            errors=errors,
        )

    @staticmethod
    def _selected_frames(plan: MultiFramePlan, frame_index: int | None) -> list[FrameUploadPlan]:
        if frame_index is None:
            return plan.frames
        selected = [frame for frame in plan.frames if frame.frame_index == frame_index]
        if not selected:
            raise CalendarAnimError(f"Animation plan has no frame {frame_index}")
        return selected
