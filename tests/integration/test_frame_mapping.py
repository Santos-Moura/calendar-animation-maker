from collections.abc import Sequence
from datetime import date
from pathlib import Path

import pytest
from PIL import Image

from calendar_anim.calendar.fake import FakeCalendarGateway
from calendar_anim.calendar.frame_mapping.artifacts import write_frame_mapping_artifacts
from calendar_anim.calendar.frame_mapping.mapper import build_single_frame_plan
from calendar_anim.calendar.frame_mapping.models import FrameMappingMode
from calendar_anim.calendar.frame_mapping.service import SingleFrameMappingService
from calendar_anim.calendar.lab import LabCalendarService
from calendar_anim.calendar.local_config import CalendarConfigStore
from calendar_anim.calendar.models import CalendarEventDraft, CalendarWriteResult
from calendar_anim.exceptions import CalendarAnimError
from tests.factories import make_manifest, make_ready_calibration_profile

pytestmark = pytest.mark.integration


def _plan(
    run_id: str = "frame-test",
    mapping_mode: FrameMappingMode = FrameMappingMode.SPARSE,
):
    return build_single_frame_plan(
        make_manifest(),
        make_ready_calibration_profile(),
        frame_index=0,
        anchor_date=date(2026, 9, 7),
        run_id=run_id,
        max_execute_events=1200,
        mapping_mode=mapping_mode,
    )


def test_mapping_artifacts_include_plan_report_and_previews(tmp_path: Path) -> None:
    source = tmp_path / "input.png"
    Image.new("RGB", (4, 4), "#808080").save(source)
    output = tmp_path / "mapping"
    plan = _plan()
    write_frame_mapping_artifacts(plan, source, output)
    assert {path.name for path in output.iterdir()} == {
        "frame-plan.json",
        "mapping-report.txt",
        "source-frame.png",
        "mapped-preview.png",
        "mapped-debug.png",
        "execution-result.json",
    }
    report = (output / "mapping-report.txt").read_text(encoding="utf-8")
    assert "Source grid: 4x4" in report
    assert "Target grid: 42x24" in report
    assert "Mapping mode: sparse" in report
    assert "Expanded source cells:" in report
    assert "Calendar events:" in report
    assert "Sparse estimate:" in report
    assert "Full-grid estimate: 1008 events" in report
    assert "Cells per event: 1.00" in report
    assert Image.open(output / "mapped-preview.png").size == (840, 480)
    assert Image.open(output / "mapped-debug.png").size == (920, 570)


def test_full_grid_artifacts_serialize_background_and_solid_canvas(tmp_path: Path) -> None:
    source = tmp_path / "input.png"
    Image.new("RGB", (4, 4), "#808080").save(source)
    output = tmp_path / "full-grid"
    plan = _plan(mapping_mode=FrameMappingMode.FULL_GRID)
    write_frame_mapping_artifacts(plan, source, output)
    report = (output / "mapping-report.txt").read_text(encoding="utf-8")
    serialized = (output / "frame-plan.json").read_text(encoding="utf-8")
    assert "Mapping mode: full-grid" in report
    assert "Background colorId: 8" in report
    assert "Total logical cells: 1008" in report
    assert "Calendar events: 1008" in report
    assert '"mapping_mode": "full-grid"' in serialized
    assert '"background_color_id": "8"' in serialized
    assert Image.open(output / "mapped-preview.png").getpixel((0, 0)) == (97, 97, 97)


def test_execute_is_idempotent_and_uses_private_frame_metadata(tmp_path: Path) -> None:
    gateway = FakeCalendarGateway()
    service = SingleFrameMappingService(
        gateway,
        LabCalendarService(gateway, CalendarConfigStore(tmp_path / "calendar.json")),
    )
    plan = _plan()
    result = service.execute(plan)
    assert result.executed is True
    assert result.planned_events == plan.event_count
    assert result.created_events == plan.event_count
    assert result.foreground_created == plan.event_count
    assert result.background_created == 0
    calendar_events = gateway.events[result.calendar_id or ""]
    assert calendar_events[0].private_metadata["frame_index"] == "0"
    assert calendar_events[0].private_metadata["logical_x"] == "9"
    with pytest.raises(CalendarAnimError, match="already exists"):
        service.execute(plan)


def test_execute_blocks_incomplete_profile_and_event_limit(tmp_path: Path) -> None:
    gateway = FakeCalendarGateway()
    service = SingleFrameMappingService(
        gateway,
        LabCalendarService(gateway, CalendarConfigStore(tmp_path / "calendar.json")),
    )
    incomplete = _plan().model_copy(update={"profile_ready": False})
    with pytest.raises(CalendarAnimError, match="NOT READY"):
        service.execute(incomplete)
    over_limit = _plan().model_copy(update={"max_execute_events": 1})
    with pytest.raises(CalendarAnimError, match="configured execute limit"):
        service.execute(over_limit)
    assert gateway.create_calendar_calls == 0


class PartialFailureGateway(FakeCalendarGateway):
    def create_events(
        self, calendar_id: str, events: Sequence[CalendarEventDraft]
    ) -> CalendarWriteResult:
        first = super().create_events(calendar_id, events[:1])
        return CalendarWriteResult(
            created_event_ids=first.created_event_ids,
            created_event_indexes=first.created_event_indexes,
            failed_events=len(events) - 1,
            errors=["simulated partial failure"],
        )


def test_partial_failure_preserves_created_ids_and_counts(tmp_path: Path) -> None:
    gateway = PartialFailureGateway()
    service = SingleFrameMappingService(
        gateway,
        LabCalendarService(gateway, CalendarConfigStore(tmp_path / "calendar.json")),
    )
    result = service.execute(_plan("partial"))
    assert result.created_events == 1
    assert result.failed_events == result.planned_events - 1
    assert result.created_event_ids == ["fake-event-1"]
    assert result.foreground_created == 1
    assert result.background_created == 0
    assert result.errors == ["simulated partial failure"]


def test_full_grid_execute_reports_foreground_and_background_created(tmp_path: Path) -> None:
    gateway = FakeCalendarGateway()
    service = SingleFrameMappingService(
        gateway,
        LabCalendarService(gateway, CalendarConfigStore(tmp_path / "calendar.json")),
    )
    plan = build_single_frame_plan(
        make_manifest(),
        make_ready_calibration_profile(),
        frame_index=0,
        anchor_date=date(2026, 9, 7),
        run_id="full-grid-execute",
        max_execute_events=1200,
        mapping_mode=FrameMappingMode.FULL_GRID,
        calendar_background_color_id="8",
    )
    result = service.execute(plan)
    assert result.created_events == 42 * 24
    assert result.foreground_created == plan.statistics.foreground_events
    assert result.background_created == plan.statistics.background_events
    assert result.foreground_created + result.background_created == result.created_events
    matches = gateway.find_events_by_private_metadata(
        result.calendar_id or "",
        {
            "animation_id": plan.animation_id,
            "run_id": plan.run_id,
            "frame_index": "0",
        },
    )
    assert len(matches) == 42 * 24
    deleted = gateway.delete_events(result.calendar_id or "", [event.id for event in matches])
    assert deleted.deleted_events == 42 * 24


def test_full_grid_partial_failure_counts_created_background_role(tmp_path: Path) -> None:
    gateway = PartialFailureGateway()
    service = SingleFrameMappingService(
        gateway,
        LabCalendarService(gateway, CalendarConfigStore(tmp_path / "calendar.json")),
    )
    plan = build_single_frame_plan(
        make_manifest(),
        make_ready_calibration_profile(),
        frame_index=0,
        anchor_date=date(2026, 9, 7),
        run_id="full-grid-partial",
        max_execute_events=1200,
        mapping_mode=FrameMappingMode.FULL_GRID,
    )
    result = service.execute(plan)
    assert plan.events[0].private_metadata["cell_role"] == "background"
    assert result.created_events == 1
    assert result.foreground_created == 0
    assert result.background_created == 1
    assert result.failed_events == plan.event_count - 1
