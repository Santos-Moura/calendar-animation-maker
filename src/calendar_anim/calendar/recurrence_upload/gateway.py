from datetime import datetime
from typing import Any, cast

from googleapiclient.errors import HttpError

from calendar_anim.calendar.google_gateway import GoogleCalendarGateway
from calendar_anim.calendar.multi_frame.retry import (
    http_error_reasons,
    is_calendar_usage_quota_exception,
    is_rate_limit_exception,
    is_retryable_exception,
)
from calendar_anim.calendar.recurrence_compaction.models import RecurringParentPlan


class ParentInsertError(Exception):
    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        google_reason: str | None = None,
        rate_limited: bool = False,
        quota_exceeded: bool = False,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.google_reason = google_reason
        self.rate_limited = rate_limited
        self.quota_exceeded = quota_exceeded
        self.retryable = retryable


class GoogleRecurrenceUploadGateway(GoogleCalendarGateway):
    def parent_body(self, parent: RecurringParentPlan) -> dict[str, object]:
        signature = parent.signature
        body: dict[str, object] = {
            "id": parent.parent_id,
            "summary": signature.summary,
            "start": {
                "dateTime": parent.start.isoformat(),
                "timeZone": signature.timezone,
            },
            "end": {
                "dateTime": parent.end.isoformat(),
                "timeZone": signature.timezone,
            },
            "transparency": signature.transparency,
            "visibility": signature.visibility,
            "eventType": signature.event_type,
            "extendedProperties": {"private": parent.private_metadata},
        }
        if signature.color_id:
            body["colorId"] = signature.color_id
        if parent.recurrence:
            body["recurrence"] = parent.recurrence
        return body

    def insert_parent(self, calendar_id: str, parent: RecurringParentPlan) -> str:
        try:
            self._pace_write()
            response = (
                self.service.events()
                .insert(
                    calendarId=calendar_id,
                    body=self.parent_body(parent),
                    sendUpdates="none",
                )
                .execute()
            )
        except Exception as error:
            reasons = http_error_reasons(error) if isinstance(error, HttpError) else set()
            status = (
                int(getattr(error.resp, "status", 0) or 0) if isinstance(error, HttpError) else 0
            )
            rate_limited = is_rate_limit_exception(error)
            quota = is_calendar_usage_quota_exception(error)
            if rate_limited:
                self._record_rate_limit()
            raise ParentInsertError(
                str(error),
                status_code=status or None,
                google_reason=next(iter(sorted(reasons)), None),
                rate_limited=rate_limited,
                quota_exceeded=quota,
                retryable=is_retryable_exception(error),
            ) from error
        self._record_successful_write()
        return str(response.get("id") or parent.parent_id)

    def get_parent(self, calendar_id: str, parent_id: str) -> dict[str, Any] | None:
        try:
            return cast(
                dict[str, Any],
                self.service.events().get(calendarId=calendar_id, eventId=parent_id).execute(),
            )
        except HttpError as error:
            if int(getattr(error.resp, "status", 0) or 0) == 404:
                return None
            raise

    def parent_matches(self, remote: dict[str, Any], parent: RecurringParentPlan) -> bool:
        body = self.parent_body(parent)
        # Calendar omits fields that have their API defaults on some GET responses.
        # Normalize only those documented defaults; every visual/identity-bearing
        # value is still compared exactly before a 409/lost response is accepted.
        expected_defaults = {
            "transparency": "opaque",
            "visibility": "default",
            "eventType": "default",
        }
        if any(remote.get(field) != body.get(field) for field in ("id", "summary", "colorId")):
            return False
        if any(remote.get(field) != body.get(field) for field in ("start", "end", "recurrence")):
            return False
        extended = remote.get("extendedProperties")
        if not isinstance(extended, dict) or extended.get("private") != parent.private_metadata:
            return False
        return all(
            remote.get(field, default) == body.get(field, default)
            for field, default in expected_defaults.items()
        )

    def list_window(self, calendar_id: str, start: datetime, end: datetime) -> list[dict[str, Any]]:
        page_token: str | None = None
        found: list[dict[str, Any]] = []
        while True:
            response = (
                self.service.events()
                .list(
                    calendarId=calendar_id,
                    singleEvents=True,
                    showDeleted=False,
                    timeMin=start.isoformat(),
                    timeMax=end.isoformat(),
                    pageToken=page_token,
                )
                .execute()
            )
            found.extend(response.get("items", []))
            page_token = response.get("nextPageToken")
            if not page_token:
                return found

    def find_bulk_parents(self, calendar_id: str, run_id: str) -> list[dict[str, Any]]:
        page_token: str | None = None
        found: list[dict[str, Any]] = []
        filters = [
            "calendar_profile=account-b",
            "generated_by=calendar-anim",
            f"run_id={run_id}",
        ]
        while True:
            response = (
                self.service.events()
                .list(
                    calendarId=calendar_id,
                    singleEvents=False,
                    showDeleted=False,
                    privateExtendedProperty=filters,
                    pageToken=page_token,
                )
                .execute()
            )
            found.extend(response.get("items", []))
            page_token = response.get("nextPageToken")
            if not page_token:
                return found
