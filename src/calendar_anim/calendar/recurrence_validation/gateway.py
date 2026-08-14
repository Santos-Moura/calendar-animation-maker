from collections.abc import Mapping
from datetime import datetime
from typing import Any

from googleapiclient.errors import HttpError

from calendar_anim.calendar.google_gateway import GoogleCalendarGateway
from calendar_anim.calendar.multi_frame.retry import (
    http_error_reasons,
    is_calendar_usage_quota_exception,
    is_rate_limit_exception,
)
from calendar_anim.calendar.recurrence_validation.models import ValidationResourcePlan


class ValidationRemoteResource:
    def __init__(self, event_id: str, metadata: Mapping[str, str]) -> None:
        self.event_id = event_id
        self.metadata = dict(metadata)


class ValidationInsertError(Exception):
    def __init__(
        self,
        message: str,
        *,
        status_code: int | None,
        google_reason: str | None,
        rate_limited: bool,
        quota_exceeded: bool,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.google_reason = google_reason
        self.rate_limited = rate_limited
        self.quota_exceeded = quota_exceeded


class GoogleRecurrenceValidationGateway(GoogleCalendarGateway):
    def list_window_resources(
        self,
        calendar_id: str,
        start: datetime,
        end: datetime,
    ) -> list[ValidationRemoteResource]:
        return self._list_resources(
            calendar_id,
            single_events=True,
            time_min=start.isoformat(),
            time_max=end.isoformat(),
        )

    def find_validation_resources(
        self,
        calendar_id: str,
        metadata: Mapping[str, str],
    ) -> list[ValidationRemoteResource]:
        filters = [f"{key}={value}" for key, value in sorted(metadata.items())]
        return self._list_resources(
            calendar_id,
            single_events=False,
            private_extended_property=filters,
        )

    def _list_resources(
        self,
        calendar_id: str,
        *,
        single_events: bool,
        time_min: str | None = None,
        time_max: str | None = None,
        private_extended_property: list[str] | None = None,
    ) -> list[ValidationRemoteResource]:
        page_token: str | None = None
        found: list[ValidationRemoteResource] = []
        while True:
            request: dict[str, Any] = {
                "calendarId": calendar_id,
                "singleEvents": single_events,
                "showDeleted": False,
                "pageToken": page_token,
            }
            if time_min is not None:
                request["timeMin"] = time_min
            if time_max is not None:
                request["timeMax"] = time_max
            if private_extended_property:
                request["privateExtendedProperty"] = private_extended_property
            response = self.service.events().list(**request).execute()
            for item in response.get("items", []):
                metadata = {
                    str(key): str(value)
                    for key, value in item.get("extendedProperties", {}).get("private", {}).items()
                }
                found.append(ValidationRemoteResource(str(item["id"]), metadata))
            page_token = response.get("nextPageToken")
            if not page_token:
                return found

    def insert_validation_resource(
        self,
        calendar_id: str,
        resource: ValidationResourcePlan,
    ) -> str:
        try:
            response = (
                self.service.events()
                .insert(
                    calendarId=calendar_id,
                    body=resource.google_body(),
                    sendUpdates="none",
                )
                .execute()
            )
        except HttpError as error:
            if int(getattr(error.resp, "status", 0) or 0) == 409:
                return resource.event_id
            reasons = http_error_reasons(error)
            raise ValidationInsertError(
                str(error),
                status_code=int(getattr(error.resp, "status", 0) or 0) or None,
                google_reason=next(iter(sorted(reasons)), None),
                rate_limited=is_rate_limit_exception(error),
                quota_exceeded=is_calendar_usage_quota_exception(error),
            ) from error
        return str(response.get("id") or resource.event_id)
