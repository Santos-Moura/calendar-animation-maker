import json
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
    def __init__(self, status: int, reason: str | None = None) -> None:
        self.status = status
        self.reason = reason

    def execute(self) -> dict[str, str]:
        content = (
            json.dumps(
                {
                    "error": {
                        "code": self.status,
                        "errors": [{"reason": self.reason}],
                    }
                }
            ).encode()
            if self.reason is not None
            else b"simulated"
        )
        raise HttpError(Response({"status": str(self.status)}), content)


class FailingEventsResource(RecordingEventsResource):
    def __init__(self, status: int, reason: str | None = None) -> None:
        super().__init__()
        self.status = status
        self.reason = reason

    def insert(self, **kwargs: Any) -> FailingInsert:
        self.calls.append(kwargs)
        return FailingInsert(self.status, self.reason)


class FailingGoogleService(RecordingGoogleService):
    def __init__(self, status: int, reason: str | None = None) -> None:
        self.events_resource = FailingEventsResource(status, reason)


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


@pytest.mark.parametrize(
    ("reason", "retryable"),
    [
        ("rateLimitExceeded", True),
        ("userRateLimitExceeded", True),
        ("quotaExceeded", False),
        ("forbidden", False),
        (None, False),
    ],
)
def test_google_gateway_only_retries_temporary_403_reasons(
    reason: str | None, retryable: bool
) -> None:
    service = FailingGoogleService(403, reason)
    gateway = GoogleCalendarGateway(service)
    start = datetime(2026, 9, 21, 6, 0, tzinfo=ZoneInfo("America/Sao_Paulo"))
    draft = CalendarEventDraft(
        start=start,
        end=start + timedelta(minutes=30),
        summary="",
        private_metadata={"run_id": "retry-403-test"},
    )

    result = gateway.create_events("calendar-id", [draft])

    assert result.failed_events == 1
    assert result.failures[0].status_code == 403
    assert result.failures[0].retryable is retryable


@pytest.mark.parametrize("status", [403, 429])
def test_google_gateway_stops_batch_after_first_rate_limit(status: int) -> None:
    service = FailingGoogleService(status, "rateLimitExceeded")
    gateway = GoogleCalendarGateway(service)
    gateway.configure_write_pacing(0.75)
    start = datetime(2026, 9, 21, 6, 0, tzinfo=ZoneInfo("America/Sao_Paulo"))
    drafts = [
        CalendarEventDraft(
            start=start + timedelta(minutes=index),
            end=start + timedelta(minutes=index + 1),
            summary="",
            private_metadata={"event": str(index)},
        )
        for index in range(50)
    ]

    result = gateway.create_events("calendar-id", drafts)

    assert len(service.events_resource.calls) == 1
    assert result.failed_events == 50
    assert len(result.failures) == 50
    assert all(failure.retryable for failure in result.failures)
    assert all(failure.reason == "rateLimitExceeded" for failure in result.failures)
    assert result.rate_limit_exceeded_count == 1
    assert result.quota_exceeded_count == 0
    assert result.quota_circuit_breaker_count == 0
    assert gateway.current_write_interval_seconds == pytest.approx(1.125)


def test_google_gateway_opens_quota_circuit_without_request_storm() -> None:
    service = FailingGoogleService(403, "quotaExceeded")
    gateway = GoogleCalendarGateway(service)
    gateway.configure_write_pacing(0.75)
    start = datetime(2026, 9, 21, 6, 0, tzinfo=ZoneInfo("America/Sao_Paulo"))
    drafts = [
        CalendarEventDraft(
            start=start + timedelta(minutes=index),
            end=start + timedelta(minutes=index + 1),
            summary="",
            private_metadata={"event": str(index)},
        )
        for index in range(50)
    ]

    result = gateway.create_events("calendar-id", drafts)

    assert len(service.events_resource.calls) == 1
    assert result.failed_events == 50
    assert len(result.failures) == 50
    assert all(not failure.retryable for failure in result.failures)
    assert all(failure.reason == "quotaExceeded" for failure in result.failures)
    assert result.rate_limit_exceeded_count == 0
    assert result.quota_exceeded_count == 1
    assert result.adaptive_rate_limit_cooldowns == 0
    assert result.quota_circuit_breaker_count == 1
    assert gateway.current_write_interval_seconds == pytest.approx(0.75)


def test_google_gateway_pacing_snapshot_restores_adaptive_progress() -> None:
    gateway = GoogleCalendarGateway(RecordingGoogleService())
    gateway.configure_write_pacing(
        0.75,
        current_interval_seconds=2.25,
        successful_writes_since_rate_limit=17,
    )
    snapshot = gateway.write_pacing_snapshot()
    restored = GoogleCalendarGateway(RecordingGoogleService())

    restored.restore_write_pacing(snapshot)

    assert restored.minimum_write_interval_seconds == 0.75
    assert restored.current_write_interval_seconds == 2.25
    assert restored.write_pacing_snapshot().successful_writes_since_rate_limit == 17


def test_google_gateway_spaces_write_starts_at_configured_interval() -> None:
    service = RecordingGoogleService()
    gateway = GoogleCalendarGateway(service)
    current = [0.0]
    sleeps: list[float] = []

    def sleeper(seconds: float) -> None:
        sleeps.append(seconds)
        current[0] += seconds

    gateway.configure_write_pacing(
        0.75,
        clock=lambda: current[0],
        sleeper=sleeper,
    )
    start = datetime(2026, 9, 21, 6, 0, tzinfo=ZoneInfo("America/Sao_Paulo"))
    drafts = [
        CalendarEventDraft(
            start=start + timedelta(minutes=index),
            end=start + timedelta(minutes=index + 1),
            summary="",
            private_metadata={"event": str(index)},
        )
        for index in range(3)
    ]

    result = gateway.create_events("calendar-id", drafts)

    assert result.failed_events == 0
    assert sleeps == [0.75, 0.75]
    assert current[0] == 1.5


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
