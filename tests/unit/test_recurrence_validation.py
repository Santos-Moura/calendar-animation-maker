from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from calendar_anim.calendar.frame_mapping.models import (
    EventCompressionMode,
    FrameMappingMode,
    FrameMappingStatistics,
    SingleFrameCalendarPlan,
)
from calendar_anim.calendar.models import (
    CalendarDeleteResult,
    CalendarEventDraft,
    CalendarInfo,
)
from calendar_anim.calendar.recurrence_validation.artifacts import RecurrenceValidationStore
from calendar_anim.calendar.recurrence_validation.gateway import (
    GoogleRecurrenceValidationGateway,
    ValidationInsertError,
    ValidationRemoteResource,
)
from calendar_anim.calendar.recurrence_validation.models import (
    ValidationResourceRole,
    ValidationStatus,
    ValidationUploadState,
)
from calendar_anim.calendar.recurrence_validation.planner import (
    build_recurrence_validation_plan,
)
from calendar_anim.calendar.recurrence_validation.service import (
    RecurrenceValidationService,
)
from calendar_anim.calendar.subcolumn_ordering import SubcolumnOrderStrategy
from calendar_anim.exceptions import CalendarAnimError


class SourceStore:
    def __init__(self, frame_plan: SingleFrameCalendarPlan) -> None:
        self.frame_plan = frame_plan

    def load_plan(self, _run_id: str) -> object:
        return object()

    def load_frame_plan(self, _plan: object, _frame_index: int) -> SingleFrameCalendarPlan:
        return self.frame_plan


def source_frame_plan() -> SingleFrameCalendarPlan:
    zone = ZoneInfo("America/Sao_Paulo")
    summary = "\u200b\u200b"
    event = CalendarEventDraft(
        frame_index=23,
        block_index=0,
        start=datetime(2028, 3, 19, 6, 0, tzinfo=zone),
        end=datetime(2028, 3, 19, 8, 15, tzinfo=zone),
        color_id="1",
        color_hex="#7986CB",
        summary=summary,
        private_metadata={"source": "test"},
    )
    statistics = FrameMappingStatistics(
        source_blocks=1,
        expanded_logical_cells=1,
        non_background_cells=1,
        mapped_cells=1,
        calendar_events=1,
        unique_calendar_colors=1,
        cells_per_event=1,
        compression_ratio=1,
    )
    return SingleFrameCalendarPlan(
        animation_id="cayde-final-3fps",
        run_id="source-frame-0023",
        frame_index=23,
        timezone="America/Sao_Paulo",
        week_start_date=date(2028, 3, 19),
        source_grid_width=126,
        source_grid_height=72,
        target_grid_width=126,
        target_grid_height=72,
        columns_per_day=18,
        mapping_mode=FrameMappingMode.FULL_GRID,
        event_compression=EventCompressionMode.SYNCHRONIZED_HORIZONTAL_BANDS,
        background_color_id="1",
        foreground_color_ids=["1", "2", "3", "4"],
        profile_ready=True,
        horizontal_strategy="independent-cells",
        subcolumn_order_strategy=SubcolumnOrderStrategy.ZERO_WIDTH,
        subcolumn_order_keys=[summary],
        max_execute_events=5200,
        statistics=statistics,
        mapped_cells=[],
        events=[event],
    )


def validation_plan():  # type: ignore[no-untyped-def]
    return build_recurrence_validation_plan(
        SourceStore(source_frame_plan()),  # type: ignore[arg-type]
        validation_id="recurrence-rdate-smallest-real-01",
        source_run_id="cayde-final-126x72-3fps-36s-01",
        source_frame_index=23,
        source_event_index=0,
        start_week=date(2029, 12, 2),
    )


def test_plan_has_one_parent_two_rdates_three_controls_and_exact_visual_properties() -> None:
    plan = validation_plan()
    parent = next(
        resource
        for resource in plan.resources
        if resource.role is ValidationResourceRole.RECURRING_PARENT
    )
    controls = [
        resource
        for resource in plan.resources
        if resource.role is ValidationResourceRole.STANDALONE_CONTROL
    ]

    assert len(plan.weeks) == 6
    assert len(parent.recurrence) == 1
    assert parent.recurrence == ["RDATE;TZID=America/Sao_Paulo:20291216T060000,20291230T060000"]
    assert len(controls) == 3
    assert plan.expected_events_insert_calls == 4
    for resource in plan.resources:
        assert resource.summary == "\u200b\u200b"
        assert resource.color_id == "1"
        assert resource.timezone == "America/Sao_Paulo"
        assert resource.end - resource.start == timedelta(hours=2, minutes=15)
        body = resource.google_body()
        assert body["summary"] == "\u200b\u200b"
        assert body["colorId"] == "1"
        assert body["start"]["timeZone"] == "America/Sao_Paulo"  # type: ignore[index]
        assert body["end"]["timeZone"] == "America/Sao_Paulo"  # type: ignore[index]


def test_plan_is_deterministic() -> None:
    assert validation_plan() == validation_plan()


def test_account_b_plan_scopes_every_resource_and_rejects_account_a_selection(
    tmp_path: Path,
) -> None:
    plan = build_recurrence_validation_plan(
        SourceStore(source_frame_plan()),  # type: ignore[arg-type]
        validation_id="recurrence-rdate-account-b-01",
        source_run_id="cayde-final-126x72-3fps-36s-01",
        source_frame_index=23,
        source_event_index=0,
        start_week=date(2030, 2, 3),
        calendar_profile="account-b",
        calendar_name="Calendar Animation Lab B",
    )

    assert plan.calendar_profile == "account-b"
    assert plan.calendar_name == "Calendar Animation Lab B"
    assert all(
        resource.private_metadata["calendar_profile"] == "account-b" for resource in plan.resources
    )
    with pytest.raises(CalendarAnimError, match="different Calendar profile"):
        state = ValidationUploadState(
            validation_id=plan.validation_id,
            calendar_profile="account-a",
            updated_at=datetime.now(UTC),
        )
        store = RecurrenceValidationStore(tmp_path)
        store.save_state(state)
        service = RecurrenceValidationService(  # type: ignore[arg-type]
            FakeValidationGateway(), store, lambda _name, _timezone: (calendar(), False)
        )
        service.upload(plan)


class ExecutableResponse:
    def __init__(self, value: dict[str, object]) -> None:
        self.value = value

    def execute(self) -> dict[str, object]:
        return self.value


class RecordingEvents:
    def __init__(self) -> None:
        self.insert_calls: list[dict[str, object]] = []
        self.list_calls: list[dict[str, object]] = []

    def insert(self, **kwargs: object) -> ExecutableResponse:
        self.insert_calls.append(kwargs)
        body = kwargs["body"]
        assert isinstance(body, dict)
        return ExecutableResponse({"id": body["id"]})

    def list(self, **kwargs: object) -> ExecutableResponse:
        self.list_calls.append(kwargs)
        return ExecutableResponse({"items": []})


class RecordingService:
    def __init__(self) -> None:
        self.events_resource = RecordingEvents()

    def events(self) -> RecordingEvents:
        return self.events_resource


def test_google_gateway_sends_rdate_timezone_and_lists_masters_for_cleanup() -> None:
    plan = validation_plan()
    parent = plan.resources[0]
    google = RecordingService()
    gateway = GoogleRecurrenceValidationGateway(google)

    gateway.insert_validation_resource("calendar-id", parent)
    gateway.find_validation_resources(
        "calendar-id", RecurrenceValidationService.metadata(plan.validation_id)
    )

    insert = google.events_resource.insert_calls[0]
    body = insert["body"]
    assert isinstance(body, dict)
    assert insert["sendUpdates"] == "none"
    assert body["recurrence"] == parent.recurrence
    assert body["summary"] == "\u200b\u200b"
    assert body["colorId"] == "1"
    assert body["start"] == {
        "dateTime": "2029-12-02T06:00:00-03:00",
        "timeZone": "America/Sao_Paulo",
    }
    listed = google.events_resource.list_calls[0]
    assert listed["singleEvents"] is False
    assert listed["privateExtendedProperty"] == [
        "calendar_profile=account-a",
        "generated_by=calendar-anim-recurrence-validation",
        "validation_id=recurrence-rdate-smallest-real-01",
    ]


class FakeValidationGateway:
    def __init__(self) -> None:
        self.window: list[ValidationRemoteResource] = []
        self.remote: list[ValidationRemoteResource] = []
        self.inserted: list[str] = []
        self.deleted: list[str] = []
        self.failure_at: int | None = None
        self.failure: ValidationInsertError | None = None
        self.requested_metadata: list[dict[str, str]] = []

    def list_window_resources(
        self, _calendar_id: str, _start: datetime, _end: datetime
    ) -> list[ValidationRemoteResource]:
        return self.window

    def find_validation_resources(
        self, _calendar_id: str, metadata: dict[str, str]
    ) -> list[ValidationRemoteResource]:
        self.requested_metadata.append(metadata)
        return [
            resource
            for resource in self.remote
            if all(resource.metadata.get(key) == value for key, value in metadata.items())
        ]

    def insert_validation_resource(self, _calendar_id: str, resource: Any) -> str:
        if self.failure_at == len(self.inserted) and self.failure is not None:
            raise self.failure
        self.inserted.append(resource.event_id)
        return resource.event_id

    def delete_events(self, _calendar_id: str, event_ids: list[str]) -> CalendarDeleteResult:
        self.deleted.extend(event_ids)
        return CalendarDeleteResult(deleted_events=len(event_ids))


def calendar() -> CalendarInfo:
    return CalendarInfo(
        id="lab-id",
        name="Calendar Animation Lab",
        description="lab",
        timezone="America/Sao_Paulo",
    )


def service(tmp_path: Path, gateway: FakeValidationGateway) -> RecurrenceValidationService:
    return RecurrenceValidationService(
        gateway,  # type: ignore[arg-type]
        RecurrenceValidationStore(tmp_path),
        lambda _name, _timezone: (calendar(), False),
    )


def test_upload_uses_exactly_four_insert_calls_and_checkpoints(tmp_path: Path) -> None:
    plan = validation_plan()
    gateway = FakeValidationGateway()
    state = service(tmp_path, gateway).upload(plan)

    assert state.status is ValidationStatus.COMPLETED
    assert state.events_insert_calls == 4
    assert gateway.inserted == [resource.event_id for resource in plan.resources]
    persisted = RecurrenceValidationStore(tmp_path).load_state(plan.validation_id)
    assert persisted == state


def test_preflight_refuses_unrelated_event_before_insert(tmp_path: Path) -> None:
    plan = validation_plan()
    gateway = FakeValidationGateway()
    gateway.window = [ValidationRemoteResource("existing-animation", {"run_id": "final"})]

    with pytest.raises(CalendarAnimError, match="unrelated event"):
        service(tmp_path, gateway).upload(plan)

    assert gateway.inserted == []
    assert RecurrenceValidationStore(tmp_path).load_state(plan.validation_id) is None


def test_quota_and_rate_limit_are_recorded_without_more_inserts(
    tmp_path: Path,
    rate_limited: bool,
    quota_exceeded: bool,
) -> None:
    plan = validation_plan()
    gateway = FakeValidationGateway()
    gateway.failure_at = 1
    gateway.failure = ValidationInsertError(
        "Calendar rejected request",
        status_code=403,
        google_reason="quotaExceeded" if quota_exceeded else "rateLimitExceeded",
        rate_limited=rate_limited,
        quota_exceeded=quota_exceeded,
    )

    with pytest.raises(CalendarAnimError, match="stopped safely"):
        service(tmp_path, gateway).upload(plan)

    state = RecurrenceValidationStore(tmp_path).load_state(plan.validation_id)
    assert state is not None
    assert state.status is ValidationStatus.PARTIAL
    assert state.events_insert_calls == 2
    assert state.rate_limit_exceeded_count == int(rate_limited)
    assert state.quota_exceeded_count == int(quota_exceeded)
    assert len(gateway.inserted) == 1


test_quota_and_rate_limit_are_recorded_without_more_inserts = pytest.mark.parametrize(
    ("rate_limited", "quota_exceeded"),
    [(True, False), (False, True)],
)(test_quota_and_rate_limit_are_recorded_without_more_inserts)


def test_resume_reconciles_existing_resource_and_inserts_only_missing(tmp_path: Path) -> None:
    plan = validation_plan()
    gateway = FakeValidationGateway()
    first = plan.resources[0]
    metadata = RecurrenceValidationService.metadata(plan.validation_id)
    existing = ValidationRemoteResource(first.event_id, metadata)
    gateway.remote = [existing]
    gateway.window = [existing]

    state = service(tmp_path, gateway).upload(plan)

    assert state.status is ValidationStatus.COMPLETED
    assert state.events_insert_calls == 3
    assert gateway.inserted == [resource.event_id for resource in plan.resources[1:]]


def test_cleanup_deletes_only_metadata_query_results(tmp_path: Path) -> None:
    plan = validation_plan()
    gateway = FakeValidationGateway()
    gateway.remote = [
        ValidationRemoteResource(resource.event_id, resource.private_metadata)
        for resource in plan.resources
    ]

    result = service(tmp_path, gateway).cleanup(plan, calendar())

    assert result.deleted_resources == 4
    assert gateway.deleted == sorted(resource.event_id for resource in plan.resources)
    assert "existing-final-event" not in gateway.deleted


def test_account_b_cleanup_cannot_select_account_a_resources(tmp_path: Path) -> None:
    plan = build_recurrence_validation_plan(
        SourceStore(source_frame_plan()),  # type: ignore[arg-type]
        validation_id="recurrence-rdate-account-b-01",
        source_run_id="cayde-final-126x72-3fps-36s-01",
        source_frame_index=23,
        source_event_index=0,
        start_week=date(2030, 2, 3),
        calendar_profile="account-b",
        calendar_name="Calendar Animation Lab B",
    )
    gateway = FakeValidationGateway()
    gateway.remote = [
        *[
            ValidationRemoteResource(resource.event_id, resource.private_metadata)
            for resource in plan.resources
        ],
        ValidationRemoteResource(
            "account-a-resource",
            {
                "generated_by": "calendar-anim-recurrence-validation",
                "validation_id": plan.validation_id,
                "calendar_profile": "account-a",
            },
        ),
    ]

    result = service(tmp_path, gateway).cleanup(plan, calendar())

    assert result.deleted_resources == 4
    assert "account-a-resource" not in gateway.deleted
    assert gateway.requested_metadata[-1]["calendar_profile"] == "account-b"
