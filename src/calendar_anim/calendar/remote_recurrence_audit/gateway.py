from datetime import datetime
from typing import Any, cast

from calendar_anim.calendar.google_gateway import GoogleCalendarGateway


class GoogleRemoteRecurrenceAuditGateway(GoogleCalendarGateway):
    """Google adapter whose audit API surface contains read operations only."""

    def list_expanded_window(
        self, calendar_id: str, start: datetime, end: datetime
    ) -> list[dict[str, Any]]:
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
                    maxResults=2500,
                    pageToken=page_token,
                )
                .execute()
            )
            found.extend(cast(list[dict[str, Any]], response.get("items", [])))
            page_token = response.get("nextPageToken")
            if not page_token:
                return found

    def get_parent_resource(self, calendar_id: str, parent_id: str) -> dict[str, Any] | None:
        try:
            return cast(
                dict[str, Any],
                self.service.events().get(calendarId=calendar_id, eventId=parent_id).execute(),
            )
        except Exception as error:
            status = int(getattr(getattr(error, "resp", None), "status", 0) or 0)
            if status == 404:
                return None
            raise
