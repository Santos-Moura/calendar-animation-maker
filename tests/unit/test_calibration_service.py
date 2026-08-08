from datetime import date
from pathlib import Path

import pytest

from calendar_anim.calendar.calibration.patterns import build_calibration_plan
from calendar_anim.calendar.calibration.service import CalibrationService
from calendar_anim.calendar.fake import FakeCalendarGateway
from calendar_anim.calendar.lab import LabCalendarService
from calendar_anim.calendar.local_config import CalendarConfigStore
from calendar_anim.exceptions import CalendarAnimError

pytestmark = pytest.mark.unit


def make_service(tmp_path: Path) -> tuple[CalibrationService, FakeCalendarGateway]:
    gateway = FakeCalendarGateway()
    store = CalendarConfigStore(tmp_path / "calendar-config.json")
    return CalibrationService(gateway, LabCalendarService(gateway, store)), gateway


def test_execute_creates_and_then_reuses_lab_calendar(tmp_path: Path) -> None:
    service, gateway = make_service(tmp_path)
    first = build_calibration_plan("duration-scale", date(2026, 8, 10), run_id="run-one")
    second = build_calibration_plan("position-grid", date(2026, 8, 10), run_id="run-two")
    assert service.execute(first).calendar_created is True
    assert service.execute(second).calendar_created is False
    assert gateway.create_calendar_calls == 1
    assert gateway.create_event_calls == 2


def test_duplicate_run_is_rejected_before_second_write(tmp_path: Path) -> None:
    service, gateway = make_service(tmp_path)
    plan = build_calibration_plan("duration-scale", date(2026, 8, 10), run_id="same-run")
    service.execute(plan)
    with pytest.raises(CalendarAnimError, match="already has 7 events"):
        service.execute(plan)
    assert gateway.create_event_calls == 1


def test_overlap_run_creates_21_events_and_rejects_duplicate(tmp_path: Path) -> None:
    service, gateway = make_service(tmp_path)
    plan = build_calibration_plan("overlap-columns", date(2026, 8, 10), run_id="overlap-run")
    result = service.execute(plan)
    assert result.created_events == 21
    assert result.calendar_id is not None
    assert len(gateway.events[result.calendar_id]) == 21

    with pytest.raises(CalendarAnimError, match="already has 21 events"):
        service.execute(plan)
    assert gateway.create_event_calls == 1


def test_overlap_cleanup_removes_only_requested_run(tmp_path: Path) -> None:
    service, gateway = make_service(tmp_path)
    target = build_calibration_plan("overlap-columns", date(2026, 8, 10), run_id="overlap-target")
    other = build_calibration_plan("overlap-columns", date(2026, 8, 10), run_id="overlap-other")
    target_result = service.execute(target)
    service.execute(other)

    match = service.find_cleanup_matches(target.calendar_name, target.animation_id, target.run_id)
    result = service.cleanup(match)
    assert result.deleted_events == 21
    assert target_result.calendar_id is not None
    remaining = gateway.events[target_result.calendar_id]
    assert len(remaining) == 21
    assert {event.private_metadata["run_id"] for event in remaining} == {"overlap-other"}


def test_cleanup_filters_metadata_and_preserves_unrelated_events(tmp_path: Path) -> None:
    service, gateway = make_service(tmp_path)
    target = build_calibration_plan("duration-scale", date(2026, 8, 10), run_id="target-run")
    other = build_calibration_plan("position-grid", date(2026, 8, 10), run_id="other-run")
    target_result = service.execute(target)
    service.execute(other)
    assert target_result.calendar_id is not None
    gateway.add_unrelated_event(target_result.calendar_id)
    match = service.find_cleanup_matches(target.calendar_name, target.animation_id, target.run_id)
    assert len(match.events) == 7
    result = service.cleanup(match)
    assert result.deleted_events == 7
    remaining = gateway.events[target_result.calendar_id]
    assert len(remaining) == other.event_count + 1
    assert any(event.id == "unrelated" for event in remaining)


def test_cleanup_with_no_matches_changes_nothing(tmp_path: Path) -> None:
    service, gateway = make_service(tmp_path)
    plan = build_calibration_plan("duration-scale", date(2026, 8, 10), run_id="existing")
    service.execute(plan)
    match = service.find_cleanup_matches(plan.calendar_name, plan.animation_id, "missing")
    result = service.cleanup(match)
    assert result.deleted_events == 0
    assert gateway.delete_event_calls == 0


@pytest.mark.parametrize(
    ("pattern", "count"),
    [
        ("color-palette", 11),
        ("position-grid", 9),
        ("horizontal-bars", 21),
        ("subcolumn-order", 24),
    ],
)
def test_remaining_calibrations_execute_and_cleanup_only_the_requested_run(
    tmp_path: Path, pattern: str, count: int
) -> None:
    service, gateway = make_service(tmp_path)
    target = build_calibration_plan(pattern, date(2026, 8, 17), run_id=f"{pattern}-target")  # type: ignore[arg-type]
    other = build_calibration_plan(pattern, date(2026, 8, 17), run_id=f"{pattern}-other")  # type: ignore[arg-type]
    target_result = service.execute(target)
    service.execute(other)
    assert target_result.created_events == count

    match = service.find_cleanup_matches(target.calendar_name, target.animation_id, target.run_id)
    result = service.cleanup(match)
    assert result.deleted_events == count
    assert target_result.calendar_id is not None
    remaining = gateway.events[target_result.calendar_id]
    assert len(remaining) == count
    assert {event.private_metadata["run_id"] for event in remaining} == {f"{pattern}-other"}


def test_subcolumn_order_reaches_fake_gateway_in_exact_plan_order(tmp_path: Path) -> None:
    service, gateway = make_service(tmp_path)
    plan = build_calibration_plan("subcolumn-order", date(2026, 9, 7), run_id="slot-order-gateway")

    result = service.execute(plan)

    assert result.calendar_id is not None
    received = gateway.events[result.calendar_id]
    assert [event.summary for event in received] == [event.summary for event in plan.events]
    assert [event.private_metadata["creation_sequence"] for event in received] == [
        event.private_metadata["creation_sequence"] for event in plan.events
    ]
    assert [event.private_metadata["subcolumn_index"] for event in received] == [
        event.private_metadata["subcolumn_index"] for event in plan.events
    ]
