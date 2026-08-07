from collections.abc import Mapping, Sequence
from typing import Protocol

from calendar_anim.calendar.models import (
    CalendarColor,
    CalendarDeleteResult,
    CalendarEventDraft,
    CalendarEventInfo,
    CalendarInfo,
    CalendarWriteResult,
)


class CalendarGateway(Protocol):
    def get_calendar(self, calendar_id: str) -> CalendarInfo | None: ...

    def find_calendar(self, name: str, description: str) -> CalendarInfo | None: ...

    def create_calendar(self, name: str, description: str, timezone: str) -> CalendarInfo: ...

    def list_event_colors(self) -> list[CalendarColor]: ...

    def create_events(
        self, calendar_id: str, events: Sequence[CalendarEventDraft]
    ) -> CalendarWriteResult: ...

    def find_events_by_private_metadata(
        self, calendar_id: str, metadata: Mapping[str, str]
    ) -> list[CalendarEventInfo]: ...

    def delete_events(self, calendar_id: str, event_ids: Sequence[str]) -> CalendarDeleteResult: ...
