from collections.abc import Sequence
from dataclasses import dataclass

from calendar_anim.calendar.calibration.models import (
    CalibrationExecutionResult,
    CalibrationPlan,
)
from calendar_anim.calendar.gateway import CalendarGateway
from calendar_anim.calendar.lab import LabCalendarService
from calendar_anim.calendar.models import CalendarDeleteResult, CalendarEventInfo, CalendarInfo
from calendar_anim.exceptions import CalendarAnimError


def calibration_metadata(plan: CalibrationPlan) -> dict[str, str]:
    return {
        "generated_by": "calendar-anim",
        "animation_id": plan.animation_id,
        "run_id": plan.run_id,
    }


@dataclass(frozen=True)
class CleanupMatch:
    calendar: CalendarInfo | None
    events: Sequence[CalendarEventInfo]


class CalibrationService:
    def __init__(self, gateway: CalendarGateway, lab: LabCalendarService) -> None:
        self.gateway = gateway
        self.lab = lab

    def execute(self, plan: CalibrationPlan) -> CalibrationExecutionResult:
        calendar, calendar_created = self.lab.resolve(plan.calendar_name, plan.timezone)
        existing = self.gateway.find_events_by_private_metadata(
            calendar.id, calibration_metadata(plan)
        )
        if existing:
            raise CalendarAnimError(
                f"Calibration run {plan.run_id} already has {len(existing)} events "
                "in the target calendar."
            )
        result = self.gateway.create_events(calendar.id, plan.events)
        return CalibrationExecutionResult(
            executed=True,
            run_id=plan.run_id,
            animation_id=plan.animation_id,
            pattern=plan.pattern,
            calendar_id=calendar.id,
            calendar_created=calendar_created,
            created_events=result.created_events,
            failed_events=result.failed_events,
            created_event_ids=result.created_event_ids,
            errors=result.errors,
        )

    def find_cleanup_matches(
        self, calendar_name: str, animation_id: str, run_id: str
    ) -> CleanupMatch:
        calendar = self.lab.find(calendar_name)
        if not calendar:
            return CleanupMatch(calendar=None, events=[])
        metadata = {
            "generated_by": "calendar-anim",
            "animation_id": animation_id,
            "run_id": run_id,
        }
        events = self.gateway.find_events_by_private_metadata(calendar.id, metadata)
        return CleanupMatch(calendar=calendar, events=events)

    def cleanup(self, match: CleanupMatch) -> CalendarDeleteResult:
        if not match.calendar or not match.events:
            return CalendarDeleteResult()
        return self.gateway.delete_events(match.calendar.id, [event.id for event in match.events])
