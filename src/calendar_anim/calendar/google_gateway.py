from collections.abc import Mapping, Sequence
from datetime import datetime
from time import monotonic, sleep
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
    CalendarWritePacingSnapshot,
    CalendarWriteResult,
)
from calendar_anim.calendar.multi_frame.retry import (
    CALENDAR_USAGE_QUOTA_REASON,
    http_error_reasons,
    is_calendar_usage_quota_exception,
    is_rate_limit_exception,
    is_retryable_exception,
)


class GoogleCalendarGateway:
    """Google Calendar API adapter. Construction itself performs no API calls."""

    def __init__(self, service: Any) -> None:
        self.service = service
        self.minimum_write_interval_seconds = 0.0
        self.current_write_interval_seconds = 0.0
        self.previous_write_interval_seconds: float | None = None
        self.maximum_write_interval_seconds = 3.0
        self._last_write_started_at: float | None = None
        self._successful_writes_since_rate_limit = 0
        self._clock = monotonic
        self._sleeper = sleep

    def configure_write_pacing(
        self,
        minimum_interval_seconds: float,
        *,
        current_interval_seconds: float | None = None,
        successful_writes_since_rate_limit: int = 0,
        clock: Any = monotonic,
        sleeper: Any = sleep,
    ) -> None:
        if minimum_interval_seconds < 0:
            raise ValueError("minimum write interval must be non-negative")
        self.minimum_write_interval_seconds = minimum_interval_seconds
        self.current_write_interval_seconds = max(
            minimum_interval_seconds,
            current_interval_seconds or 0.0,
        )
        self.previous_write_interval_seconds = None
        self._last_write_started_at = None
        self._successful_writes_since_rate_limit = successful_writes_since_rate_limit
        self._clock = clock
        self._sleeper = sleeper

    def write_pacing_snapshot(self) -> CalendarWritePacingSnapshot:
        return CalendarWritePacingSnapshot(
            minimum_interval_seconds=self.minimum_write_interval_seconds,
            current_interval_seconds=self.current_write_interval_seconds,
            previous_interval_seconds=self.previous_write_interval_seconds,
            maximum_interval_seconds=self.maximum_write_interval_seconds,
            successful_writes_since_rate_limit=self._successful_writes_since_rate_limit,
        )

    def restore_write_pacing(self, snapshot: CalendarWritePacingSnapshot) -> None:
        self.minimum_write_interval_seconds = snapshot.minimum_interval_seconds
        self.current_write_interval_seconds = max(
            snapshot.minimum_interval_seconds,
            snapshot.current_interval_seconds,
        )
        self.previous_write_interval_seconds = snapshot.previous_interval_seconds
        self.maximum_write_interval_seconds = snapshot.maximum_interval_seconds
        self._successful_writes_since_rate_limit = snapshot.successful_writes_since_rate_limit
        self._last_write_started_at = None

    @staticmethod
    def _calendar_info(item: dict[str, Any]) -> CalendarInfo:
        return CalendarInfo(
            id=str(item["id"]),
            name=str(item.get("summary", "")),
            description=str(item.get("description", "")),
            timezone=str(item.get("timeZone", "UTC")),
            primary=bool(item.get("primary", False)),
            access_role=(str(item["accessRole"]) if item.get("accessRole") else None),
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
        rate_limit_exceeded_count = 0
        quota_exceeded_count = 0
        quota_circuit_breaker_count = 0
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
                self._pace_write()
                response = (
                    self.service.events()
                    .insert(calendarId=calendar_id, body=body, sendUpdates="none")
                    .execute()
                )
                event_id = response.get("id")
                if event_id:
                    created.append(str(event_id))
                    created_indexes.append(index)
                    self._record_successful_write()
                else:
                    message = f"Event {index} was created without a returned ID"
                    errors.append(message)
                    failures.append(CalendarWriteFailure(event_index=index, message=message))
            except HttpError as error:
                status = int(getattr(error.resp, "status", 0) or 0)
                if status == 409:
                    created.append(expected_id)
                    created_indexes.append(index)
                    self._record_successful_write()
                    continue
                reasons = http_error_reasons(error)
                reason = next(iter(sorted(reasons)), None)
                message = f"Event {index}: {error}"
                errors.append(message)
                failures.append(
                    CalendarWriteFailure(
                        event_index=index,
                        message=message,
                        retryable=is_retryable_exception(error),
                        status_code=status or None,
                        reason=reason,
                    )
                )
                rate_limited = is_rate_limit_exception(error)
                quota_exceeded = is_calendar_usage_quota_exception(error)
                if rate_limited:
                    rate_limit_exceeded_count += 1
                    self._record_rate_limit()
                if quota_exceeded:
                    quota_exceeded_count += 1
                    quota_circuit_breaker_count += 1
                if rate_limited or quota_exceeded:
                    deferred_reason = reason or (
                        CALENDAR_USAGE_QUOTA_REASON if quota_exceeded else "rateLimitExceeded"
                    )
                    deferred_message = (
                        f"Deferred after event {index} hit {deferred_reason}; no request sent"
                    )
                    for deferred_index in range(index + 1, len(events)):
                        failures.append(
                            CalendarWriteFailure(
                                event_index=deferred_index,
                                message=deferred_message,
                                retryable=rate_limited,
                                status_code=status or None,
                                reason=deferred_reason,
                            )
                        )
                    if len(events) - index - 1:
                        errors.append(
                            f"Deferred {len(events) - index - 1} event(s) after {deferred_reason}"
                        )
                    break
        return CalendarWriteResult(
            created_event_ids=created,
            created_event_indexes=created_indexes,
            failed_events=len(events) - len(created_indexes),
            errors=errors,
            failures=failures,
            rate_limit_exceeded_count=rate_limit_exceeded_count,
            quota_exceeded_count=quota_exceeded_count,
            quota_circuit_breaker_count=quota_circuit_breaker_count,
        )

    def _pace_write(self) -> None:
        if self.current_write_interval_seconds <= 0:
            return
        now = self._clock()
        if self._last_write_started_at is not None:
            remaining = self.current_write_interval_seconds - (now - self._last_write_started_at)
            if remaining > 0:
                self._sleeper(remaining)
                now = self._clock()
        self._last_write_started_at = now

    def _record_rate_limit(self) -> None:
        baseline = max(
            self.minimum_write_interval_seconds,
            self.current_write_interval_seconds,
            0.1,
        )
        self.previous_write_interval_seconds = self.current_write_interval_seconds
        self.current_write_interval_seconds = min(
            self.maximum_write_interval_seconds,
            baseline * 1.5,
        )
        self._successful_writes_since_rate_limit = 0

    def _record_successful_write(self) -> None:
        if self.current_write_interval_seconds <= self.minimum_write_interval_seconds:
            return
        self._successful_writes_since_rate_limit += 1
        if self._successful_writes_since_rate_limit < 200:
            return
        self.previous_write_interval_seconds = self.current_write_interval_seconds
        self.current_write_interval_seconds = max(
            self.minimum_write_interval_seconds,
            self.current_write_interval_seconds * 0.9,
        )
        self._successful_writes_since_rate_limit = 0

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

    def list_event_ids_in_range(
        self,
        calendar_id: str,
        time_min: datetime,
        time_max: datetime,
    ) -> list[str]:
        """List expanded event IDs in a half-open range without mutating Calendar."""

        page_token: str | None = None
        event_ids: list[str] = []
        while True:
            response = (
                self.service.events()
                .list(
                    calendarId=calendar_id,
                    timeMin=time_min.isoformat(),
                    timeMax=time_max.isoformat(),
                    singleEvents=True,
                    showDeleted=False,
                    maxResults=2500,
                    pageToken=page_token,
                )
                .execute()
            )
            event_ids.extend(str(item["id"]) for item in response.get("items", []))
            page_token = response.get("nextPageToken")
            if not page_token:
                return event_ids

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
