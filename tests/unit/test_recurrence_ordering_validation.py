from datetime import date, datetime
from pathlib import Path
from typing import Any

import pytest

from calendar_anim.calendar.models import CalendarDeleteResult, CalendarInfo
from calendar_anim.calendar.recurrence_validation.gateway import (
    ValidationInsertError,
    ValidationRemoteResource,
)
from calendar_anim.calendar.recurrence_validation.models import (
    ValidationResourceRole,
    ValidationStatus,
)
from calendar_anim.calendar.recurrence_validation.ordering import (
    ORDERING_COLOR_IDS,
    OrderingDomEvent,
    OrderingDomSnapshot,
    OrderingValidationStore,
    analyze_snapshots,
    build_ordering_validation_plan,
)
from calendar_anim.calendar.recurrence_validation.service import RecurrenceValidationService
from calendar_anim.calendar.subcolumn_ordering import (
    SubcolumnOrderStrategy,
    summary_order_keys,
)
from calendar_anim.exceptions import CalendarAnimError


class SourcePlan:
    timezone = "America/Sao_Paulo"
    subcolumn_order_strategy = SubcolumnOrderStrategy.ZERO_WIDTH
    subcolumn_order_keys = summary_order_keys(18, SubcolumnOrderStrategy.ZERO_WIDTH)


class SourceStore:
    def load_plan(self, _run_id: str) -> SourcePlan:
        return SourcePlan()


def plan():  # type: ignore[no-untyped-def]
    return build_ordering_validation_plan(
        SourceStore(),  # type: ignore[arg-type]
        source_run_id="cayde-final-126x72-3fps-36s-01",
        start_week=date(2030, 4, 7),
    )


def test_plan_uses_exact_18_keys_colors_and_36_deterministic_resources() -> None:
    first = plan()
    second = plan()

    assert first == second
    assert first.summaries == summary_order_keys(18, SubcolumnOrderStrategy.ZERO_WIDTH)
    assert first.color_ids == [ORDERING_COLOR_IDS[index % 4] for index in range(18)]
    assert len(first.resources) == first.expected_events_insert_calls == 36
    assert (
        sum(
            resource.role is ValidationResourceRole.RECURRING_PARENT for resource in first.resources
        )
        == 18
    )
    assert (
        sum(
            resource.role is ValidationResourceRole.STANDALONE_CONTROL
            for resource in first.resources
        )
        == 18
    )


def test_every_slot_has_matching_parent_control_and_three_recurring_instances() -> None:
    validation = plan()

    for slot in range(18):
        resources = [
            item
            for item in validation.resources
            if item.private_metadata["slot_index"] == str(slot)
        ]
        assert len(resources) == 2
        assert {item.summary for item in resources} == {validation.summaries[slot]}
        assert {item.color_id for item in resources} == {validation.color_ids[slot]}
        assert {item.end - item.start for item in resources}.__len__() == 1
        parent = next(
            item for item in resources if item.role is ValidationResourceRole.RECURRING_PARENT
        )
        assert parent.recurrence == ["RDATE;TZID=America/Sao_Paulo:20300414T060000,20300421T060000"]


def _snapshot(label: str, *, shift: float = 0, swapped: bool = False) -> OrderingDomSnapshot:
    validation = plan()
    events = [
        OrderingDomEvent(
            slot_index=slot,
            summary=validation.summaries[slot],
            summary_codepoints=" ".join(
                f"U+{ord(character):04X}" for character in validation.summaries[slot]
            ),
            color_id_expected=validation.color_ids[slot],
            x=shift + (17 - slot if swapped else slot) * 10,
            width=9,
            y=100,
            height=80,
            css_background_color="rgb(121, 134, 203)",
        )
        for slot in range(18)
    ]
    ordered = sorted(events, key=lambda item: item.x)
    return OrderingDomSnapshot(
        label=label,
        week_start=date(2030, 4, 7),
        events=events,
        summaries_preserved=True,
        strictly_increasing_x=True,
        slot_order=[item.slot_index for item in ordered],
    )


def test_capture_result_passes_only_equal_refresh_navigation_and_control(tmp_path: Path) -> None:
    validation = plan()
    result = analyze_snapshots(
        validation,
        [
            _snapshot("recurring-initial"),
            _snapshot("recurring-refresh"),
            _snapshot("recurring-navigation"),
            _snapshot("recurring-week-2"),
            _snapshot("recurring-week-3"),
            _snapshot("standalone", shift=20),
        ],
        tmp_path / "comparison.png",
    )

    assert result.result == "PASS"
    assert result.summaries_preserved_18_of_18
    assert result.strict_x_ordering
    assert result.recurring_equals_standalone
    assert result.refresh_stable
    assert result.navigation_stable


def test_capture_result_is_no_go_when_standalone_slot_order_changes(tmp_path: Path) -> None:
    validation = plan()
    result = analyze_snapshots(
        validation,
        [
            _snapshot("recurring-initial"),
            _snapshot("recurring-refresh"),
            _snapshot("recurring-navigation"),
            _snapshot("standalone", swapped=True),
        ],
        tmp_path / "comparison.png",
    )

    assert result.result == "NO-GO"
    assert not result.recurring_equals_standalone
    assert not result.strict_x_ordering


def test_ordering_store_round_trip_preserves_exact_unicode(tmp_path: Path) -> None:
    store = OrderingValidationStore(tmp_path)
    original = plan()

    store.save_plan(original)

    assert store.load_plan(original.validation_id) == original


class FakeGateway:
    def __init__(self) -> None:
        self.window: list[ValidationRemoteResource] = []
        self.remote: list[ValidationRemoteResource] = []
        self.inserted: list[str] = []
        self.failure_at: int | None = None

    def list_window_resources(
        self, _calendar_id: str, _start: datetime, _end: datetime
    ) -> list[ValidationRemoteResource]:
        return self.window

    def find_validation_resources(
        self, _calendar_id: str, metadata: dict[str, str]
    ) -> list[ValidationRemoteResource]:
        return [
            item
            for item in self.remote
            if all(item.metadata.get(key) == value for key, value in metadata.items())
        ]

    def insert_validation_resource(self, _calendar_id: str, resource: Any) -> str:
        if self.failure_at == len(self.inserted):
            raise ValidationInsertError(
                "quota",
                status_code=403,
                google_reason="quotaExceeded",
                rate_limited=False,
                quota_exceeded=True,
            )
        self.inserted.append(resource.event_id)
        return resource.event_id

    def delete_events(self, _calendar_id: str, event_ids: list[str]) -> CalendarDeleteResult:
        return CalendarDeleteResult(deleted_events=len(event_ids))


def _service(tmp_path: Path, gateway: FakeGateway) -> RecurrenceValidationService:
    calendar = CalendarInfo(
        id="account-b-calendar",
        name="Calendar Animation Lab B",
        description="ordering",
        timezone="America/Sao_Paulo",
    )
    return RecurrenceValidationService(
        gateway,  # type: ignore[arg-type]
        OrderingValidationStore(tmp_path),
        lambda _name, _timezone: (calendar, False),
    )


def test_upload_is_exactly_36_inserts_and_checkpointed(tmp_path: Path) -> None:
    validation = plan()
    gateway = FakeGateway()

    state = _service(tmp_path, gateway).upload(validation)

    assert state.status is ValidationStatus.COMPLETED
    assert state.events_insert_calls == 36
    assert gateway.inserted == [item.event_id for item in validation.resources]


def test_preflight_stops_before_first_ordering_insert(tmp_path: Path) -> None:
    validation = plan()
    gateway = FakeGateway()
    gateway.window = [ValidationRemoteResource("unrelated", {"run_id": "final"})]

    with pytest.raises(CalendarAnimError, match="unrelated event"):
        _service(tmp_path, gateway).upload(validation)

    assert gateway.inserted == []


def test_quota_stops_immediately_without_request_storm_and_preserves_progress(
    tmp_path: Path,
) -> None:
    validation = plan()
    gateway = FakeGateway()
    gateway.failure_at = 7

    with pytest.raises(CalendarAnimError, match="stopped safely"):
        _service(tmp_path, gateway).upload(validation)

    state = OrderingValidationStore(tmp_path).load_state(validation.validation_id)
    assert state is not None
    assert state.status is ValidationStatus.PARTIAL
    assert state.events_insert_calls == 8
    assert state.quota_exceeded_count == 1
    assert len(gateway.inserted) == 7


def test_resume_reconciles_and_creates_only_missing_resources(tmp_path: Path) -> None:
    validation = plan()
    gateway = FakeGateway()
    metadata = RecurrenceValidationService.metadata(
        validation.validation_id, validation.calendar_profile
    )
    gateway.remote = [
        ValidationRemoteResource(item.event_id, metadata) for item in validation.resources[:7]
    ]
    gateway.window = list(gateway.remote)

    state = _service(tmp_path, gateway).upload(validation)

    assert state.status is ValidationStatus.COMPLETED
    assert state.events_insert_calls == 29
    assert gateway.inserted == [item.event_id for item in validation.resources[7:]]
