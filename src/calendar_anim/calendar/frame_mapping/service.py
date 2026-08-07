from typing import Final

from calendar_anim.calendar.frame_mapping.models import (
    SingleFrameCalendarPlan,
    SingleFrameExecutionResult,
)
from calendar_anim.calendar.gateway import CalendarGateway
from calendar_anim.calendar.lab import LabCalendarService
from calendar_anim.exceptions import CalendarAnimError

DEFAULT_SINGLE_FRAME_MAX_EVENTS: Final = 1200
ABSOLUTE_SINGLE_FRAME_MAX_EVENTS: Final = 2000


def single_frame_metadata(plan: SingleFrameCalendarPlan) -> dict[str, str]:
    return {
        "generated_by": "calendar-anim",
        "animation_id": plan.animation_id,
        "run_id": plan.run_id,
        "frame_index": str(plan.frame_index),
    }


class SingleFrameMappingService:
    def __init__(self, gateway: CalendarGateway, lab: LabCalendarService) -> None:
        self.gateway = gateway
        self.lab = lab

    def execute(self, plan: SingleFrameCalendarPlan) -> SingleFrameExecutionResult:
        if not plan.profile_ready:
            raise CalendarAnimError(
                "Calibration profile is NOT READY; real single-frame upload is blocked"
            )
        if plan.event_count > plan.max_execute_events:
            raise CalendarAnimError(
                f"Frame requires {plan.event_count} events, above the configured execute "
                f"limit of {plan.max_execute_events}"
            )
        if plan.max_execute_events > ABSOLUTE_SINGLE_FRAME_MAX_EVENTS:
            raise CalendarAnimError(
                f"Configured execute limit exceeds the absolute safety limit of "
                f"{ABSOLUTE_SINGLE_FRAME_MAX_EVENTS}"
            )
        calendar, calendar_created = self.lab.resolve(plan.calendar_name, plan.timezone)
        existing = self.gateway.find_events_by_private_metadata(
            calendar.id, single_frame_metadata(plan)
        )
        if existing:
            raise CalendarAnimError("Single frame run already exists.")
        result = self.gateway.create_events(calendar.id, plan.events)
        created_indexes = result.created_event_indexes
        if len(created_indexes) != result.created_events:
            created_indexes = list(range(result.created_events))
        foreground_created = sum(
            plan.events[index].private_metadata.get("cell_role", "foreground") == "foreground"
            for index in created_indexes
        )
        background_created = sum(
            plan.events[index].private_metadata.get("cell_role") == "background"
            for index in created_indexes
        )
        return SingleFrameExecutionResult(
            executed=True,
            run_id=plan.run_id,
            animation_id=plan.animation_id,
            frame_index=plan.frame_index,
            calendar_id=calendar.id,
            calendar_created=calendar_created,
            planned_events=plan.event_count,
            created_events=result.created_events,
            failed_events=result.failed_events,
            foreground_created=foreground_created,
            background_created=background_created,
            created_event_ids=result.created_event_ids,
            errors=result.errors,
        )
