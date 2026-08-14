from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta

from calendar_anim.calendar.event_identity import deterministic_event_id
from calendar_anim.calendar.models import (
    CalendarColor,
    CalendarDeleteResult,
    CalendarEventDraft,
    CalendarEventInfo,
    CalendarInfo,
    CalendarWriteResult,
)


class FakeCalendarGateway:
    """Deterministic in-memory adapter used by application tests."""

    def __init__(self) -> None:
        self.calendars: dict[str, CalendarInfo] = {}
        self.events: dict[str, list[CalendarEventInfo]] = {}
        self.create_calendar_calls = 0
        self.create_event_calls = 0
        self.delete_event_calls = 0

    def get_calendar(self, calendar_id: str) -> CalendarInfo | None:
        return self.calendars.get(calendar_id)

    def find_calendar(self, name: str, description: str) -> CalendarInfo | None:
        return next(
            (
                calendar
                for calendar in self.calendars.values()
                if calendar.name == name
                and calendar.description == description
                and not calendar.primary
            ),
            None,
        )

    def create_calendar(self, name: str, description: str, timezone: str) -> CalendarInfo:
        self.create_calendar_calls += 1
        calendar_id = f"fake-calendar-{self.create_calendar_calls}"
        calendar = CalendarInfo(
            id=calendar_id, name=name, description=description, timezone=timezone
        )
        self.calendars[calendar_id] = calendar
        self.events[calendar_id] = []
        return calendar

    def list_event_colors(self) -> list[CalendarColor]:
        return [
            CalendarColor(id="1", background="#7986CB", foreground="#FFFFFF"),
            CalendarColor(id="2", background="#33B679", foreground="#FFFFFF"),
        ]

    def create_events(
        self, calendar_id: str, events: Sequence[CalendarEventDraft]
    ) -> CalendarWriteResult:
        self.create_event_calls += 1
        created: list[str] = []
        created_indexes: list[int] = []
        for index, draft in enumerate(events):
            event_id = deterministic_event_id(draft)
            if any(existing.id == event_id for existing in self.events[calendar_id]):
                created.append(event_id)
                created_indexes.append(index)
                continue
            self.events[calendar_id].append(
                CalendarEventInfo(
                    id=event_id,
                    summary=draft.summary,
                    start=draft.start,
                    end=draft.end,
                    private_metadata=draft.private_metadata,
                )
            )
            created.append(event_id)
            created_indexes.append(index)
        return CalendarWriteResult(
            created_event_ids=created,
            created_event_indexes=created_indexes,
        )

    def find_events_by_private_metadata(
        self, calendar_id: str, metadata: Mapping[str, str]
    ) -> list[CalendarEventInfo]:
        return [
            event
            for event in self.events.get(calendar_id, [])
            if all(event.private_metadata.get(key) == value for key, value in metadata.items())
        ]

    def delete_events(self, calendar_id: str, event_ids: Sequence[str]) -> CalendarDeleteResult:
        self.delete_event_calls += 1
        ids = set(event_ids)
        before = len(self.events.get(calendar_id, []))
        self.events[calendar_id] = [
            event for event in self.events.get(calendar_id, []) if event.id not in ids
        ]
        return CalendarDeleteResult(deleted_events=before - len(self.events[calendar_id]))

    def add_unrelated_event(self, calendar_id: str) -> None:
        self.events[calendar_id].append(
            CalendarEventInfo(
                id="unrelated",
                summary="Personal event",
                start=datetime.now(UTC),
                end=datetime.now(UTC) + timedelta(hours=1),
                private_metadata={},
            )
        )
