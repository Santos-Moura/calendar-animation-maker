from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from googleapiclient.errors import HttpError
from httplib2 import Response

from calendar_anim.calendar.google_gateway import GoogleCalendarGateway
from calendar_anim.calendar.models import CalendarEventDraft

pytestmark = pytest.mark.unit


class SuccessfulInsert:
    def execute(self) -> dict[str, str]:
        return {"id": "event-1"}


class RecordingEventsResource:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def insert(self, **kwargs: Any) -> SuccessfulInsert:
        self.calls.append(kwargs)
        return SuccessfulInsert()


class RecordingGoogleService:
    def __init__(self) -> None:
        self.events_resource = RecordingEventsResource()

    def events(self) -> RecordingEventsResource:
        return self.events_resource


class FailingInsert:
    def __init__(self, status: int) -> None:
        self.status = status

    def execute(self) -> dict[str, str]:
        raise HttpError(Response({"status": str(self.status)}), b"simulated")


class FailingEventsResource(RecordingEventsResource):
    def __init__(self, status: int) -> None:
        super().__init__()
        self.status = status

    def insert(self, **kwargs: Any) -> FailingInsert:
        self.calls.append(kwargs)
        return FailingInsert(self.status)


class FailingGoogleService(RecordingGoogleService):
    def __init__(self, status: int) -> None:
        self.events_resource = FailingEventsResource(status)


def test_google_gateway_forwards_technical_summary_without_replacing_it() -> None:
    service = RecordingGoogleService()
    gateway = GoogleCalendarGateway(service)
    start = datetime(2026, 9, 21, 6, 0, tzinfo=ZoneInfo("America/Sao_Paulo"))
    draft = CalendarEventDraft(
        start=start,
        end=start + timedelta(minutes=30),
        color_id="5",
        color_hex="#F6BF26",
        summary="04",
        private_metadata={
            "subcolumn_index": "4",
            "subcolumn_order_strategy": "summary-prefix",
        },
    )

    result = gateway.create_events("calendar-id", [draft])

    assert result.created_event_ids == ["event-1"]
    call = service.events_resource.calls[0]
    assert call["calendarId"] == "calendar-id"
    assert call["sendUpdates"] == "none"
    assert call["body"]["summary"] == "04"
    assert call["body"]["colorId"] == "5"
    assert call["body"]["id"].startswith("ca")
    assert call["body"]["extendedProperties"]["private"]["subcolumn_index"] == "4"


@pytest.mark.parametrize(("status", "retryable"), [(429, True), (503, True), (400, False)])
def test_google_gateway_classifies_event_failures(status: int, retryable: bool) -> None:
    service = FailingGoogleService(status)
    gateway = GoogleCalendarGateway(service)
    start = datetime(2026, 9, 21, 6, 0, tzinfo=ZoneInfo("America/Sao_Paulo"))
    draft = CalendarEventDraft(
        start=start,
        end=start + timedelta(minutes=30),
        summary="",
        private_metadata={"run_id": "retry-test"},
    )

    result = gateway.create_events("calendar-id", [draft])

    assert result.failed_events == 1
    assert result.failures[0].status_code == status
    assert result.failures[0].retryable is retryable


def test_google_conflict_confirms_deterministic_event_already_exists() -> None:
    service = FailingGoogleService(409)
    gateway = GoogleCalendarGateway(service)
    start = datetime(2026, 9, 21, 6, 0, tzinfo=ZoneInfo("America/Sao_Paulo"))
    draft = CalendarEventDraft(
        start=start,
        end=start + timedelta(minutes=30),
        summary="",
        private_metadata={"run_id": "idempotent-test"},
    )

    result = gateway.create_events("calendar-id", [draft])

    assert result.created_events == 1
    assert result.failed_events == 0
    assert result.created_event_ids[0].startswith("ca")
