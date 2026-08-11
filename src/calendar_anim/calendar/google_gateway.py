from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

from googleapiclient.errors import HttpError

from calendar_anim.calendar.event_identity import deterministic_event_id
from calendar_anim.calendar.models import (
    CalendarColor,
    CalendarDeleteResult,
    CalendarEventDraft,
    CalendarEventInfo,
    CalendarInfo,
    CalendarWriteFailure,
    CalendarWriteResult,
)


class GoogleCalendarGateway:
    """Google Calendar API adapter. Construction itself performs no API calls."""

    def __init__(self, service: Any) -> None:
        self.service = service

    @staticmethod
    def _calendar_info(item: dict[str, Any]) -> CalendarInfo:
        return CalendarInfo(
            id=str(item["id"]),
            name=str(item.get("summary", "")),
            description=str(item.get("description", "")),
            timezone=str(item.get("timeZone", "UTC")),
            primary=bool(item.get("primary", False)),
        )

    def get_calendar(self, calendar_id: str) -> CalendarInfo | None:
        try:
            item = self.service.calendarList().get(calendarId=calendar_id).execute()
        except HttpError as error:
            if error.resp.status == 404:
                return None
            raise
        return self._calendar_info(item)

    def find_calendar(self, name: str, description: str) -> CalendarInfo | None:
        page_token: str | None = None
        while True:
            response = self.service.calendarList().list(pageToken=page_token).execute()
            for item in response.get("items", []):
                calendar = self._calendar_info(item)
                if (
                    not calendar.primary
                    and calendar.name == name
                    and calendar.description == description
                ):
                    return calendar
            page_token = response.get("nextPageToken")
            if not page_token:
                return None

    def create_calendar(self, name: str, description: str, timezone: str) -> CalendarInfo:
        item = (
            self.service.calendars()
            .insert(body={"summary": name, "description": description, "timeZone": timezone})
            .execute()
        )
        return self._calendar_info(item)

    def list_event_colors(self) -> list[CalendarColor]:
        response = self.service.colors().get().execute()
        return [
            CalendarColor(
                id=str(color_id),
                background=str(values["background"]),
                foreground=str(values["foreground"]),
            )
            for color_id, values in sorted(
                response.get("event", {}).items(), key=lambda item: int(item[0])
            )
        ]

    def create_events(
        self, calendar_id: str, events: Sequence[CalendarEventDraft]
    ) -> CalendarWriteResult:
        created: list[str] = []
        created_indexes: list[int] = []
        errors: list[str] = []
        failures: list[CalendarWriteFailure] = []
        for index, event in enumerate(events):
            expected_id = deterministic_event_id(event)
            body: dict[str, Any] = {
                "id": expected_id,
                "summary": event.summary,
                "start": {"dateTime": event.start.isoformat()},
                "end": {"dateTime": event.end.isoformat()},
                "extendedProperties": {"private": event.private_metadata},
            }
            if event.color_id:
                body["colorId"] = event.color_id
            try:
                response = (
                    self.service.events()
                    .insert(calendarId=calendar_id, body=body, sendUpdates="none")
                    .execute()
                )
                event_id = response.get("id")
                if event_id:
                    created.append(str(event_id))
                    created_indexes.append(index)
                else:
                    errors.append(f"Event {index} was created without a returned ID")
            except HttpError as error:
                status = int(getattr(error.resp, "status", 0) or 0)
                if status == 409:
                    created.append(expected_id)
                    created_indexes.append(index)
                    continue
                message = f"Event {index}: {error}"
                errors.append(message)
                failures.append(
                    CalendarWriteFailure(
                        event_index=index,
                        message=message,
                        retryable=status == 429 or 500 <= status <= 599,
                        status_code=status or None,
                    )
                )
        return CalendarWriteResult(
            created_event_ids=created,
            created_event_indexes=created_indexes,
            failed_events=len(errors),
            errors=errors,
            failures=failures,
        )

    def find_events_by_private_metadata(
        self, calendar_id: str, metadata: Mapping[str, str]
    ) -> list[CalendarEventInfo]:
        page_token: str | None = None
        found: list[CalendarEventInfo] = []
        filters = [f"{key}={value}" for key, value in sorted(metadata.items())]
        while True:
            response = (
                self.service.events()
                .list(
                    calendarId=calendar_id,
                    privateExtendedProperty=filters,
                    singleEvents=True,
                    pageToken=page_token,
                )
                .execute()
            )
            for item in response.get("items", []):
                start_value = item.get("start", {}).get("dateTime")
                end_value = item.get("end", {}).get("dateTime")
                if not start_value or not end_value:
                    continue
                found.append(
                    CalendarEventInfo(
                        id=str(item["id"]),
                        summary=str(item.get("summary", "")),
                        start=datetime.fromisoformat(start_value),
                        end=datetime.fromisoformat(end_value),
                        private_metadata={
                            str(key): str(value)
                            for key, value in item.get("extendedProperties", {})
                            .get("private", {})
                            .items()
                        },
                    )
                )
            page_token = response.get("nextPageToken")
            if not page_token:
                return found

    def delete_events(self, calendar_id: str, event_ids: Sequence[str]) -> CalendarDeleteResult:
        deleted = 0
        errors: list[str] = []
        for event_id in event_ids:
            try:
                (
                    self.service.events()
                    .delete(calendarId=calendar_id, eventId=event_id, sendUpdates="none")
                    .execute()
                )
                deleted += 1
            except HttpError as error:
                errors.append(f"Event {event_id}: {error}")
        return CalendarDeleteResult(
            deleted_events=deleted, failed_events=len(errors), errors=errors
        )
