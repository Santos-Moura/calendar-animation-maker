from collections.abc import Sequence
from pathlib import Path

from calendar_anim.calendar.models import (
    CalendarEventDraft,
    CalendarPlan,
    CalendarWriteResult,
)


class DryRunCalendarGateway:
    def __init__(self) -> None:
        self.events: list[CalendarEventDraft] = []

    def create_events(
        self, calendar_id: str, events: Sequence[CalendarEventDraft]
    ) -> CalendarWriteResult:
        self.events.extend(events)
        return CalendarWriteResult(
            created_event_ids=[f"dry-run-{index}" for index, _ in enumerate(events)]
        )

    def delete_animation_events(self, calendar_id: str, animation_id: str) -> int:
        matching = [
            event
            for event in self.events
            if event.private_metadata.get("animation_id") == animation_id
        ]
        self.events = [event for event in self.events if event not in matching]
        return len(matching)

    @staticmethod
    def export(plan: CalendarPlan, path: Path) -> None:
        path.write_text(plan.model_dump_json(indent=2) + "\n", encoding="utf-8")
