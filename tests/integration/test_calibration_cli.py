from datetime import date
from pathlib import Path

import pytest
from typer.testing import CliRunner

import calendar_anim.calendar.commands as calendar_commands
from calendar_anim.calendar.calibration.patterns import build_calibration_plan
from calendar_anim.calendar.calibration.service import CalibrationService
from calendar_anim.calendar.fake import FakeCalendarGateway
from calendar_anim.calendar.lab import LabCalendarService
from calendar_anim.calendar.local_config import CalendarConfigStore
from calendar_anim.cli import app

pytestmark = pytest.mark.integration
runner = CliRunner()


def test_cli_lists_patterns() -> None:
    result = runner.invoke(app, ["calendar", "calibration-patterns"])
    assert result.exit_code == 0, result.output
    assert "overlap-columns" in result.output
    assert "combined" in result.output


def test_cli_calibrate_is_dry_run_and_writes_artifacts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        calendar_commands,
        "_google_gateway",
        lambda: pytest.fail("dry-run must not create an API gateway"),
    )
    output = tmp_path / "calibration"
    result = runner.invoke(
        app,
        [
            "calendar",
            "calibrate",
            "--pattern",
            "overlap-columns",
            "--start-date",
            "2026-08-10",
            "--run-id",
            "cli-run",
            "--output",
            str(output),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "Execution: DRY RUN" in result.output
    assert (output / "calibration-plan.json").is_file()
    assert (output / "expected-layout.png").is_file()


def test_yes_without_execute_remains_dry_run(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        [
            "calendar",
            "calibrate",
            "--pattern",
            "duration-scale",
            "--start-date",
            "2026-08-10",
            "--run-id",
            "yes-dry-run",
            "--output",
            str(tmp_path),
            "--yes",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "no API call was made" in result.output


def test_execute_confirmation_can_be_cancelled_without_credentials(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        [
            "calendar",
            "calibrate",
            "--pattern",
            "duration-scale",
            "--start-date",
            "2026-08-10",
            "--run-id",
            "cancelled-run",
            "--output",
            str(tmp_path),
            "--execute",
        ],
        input="n\n",
    )
    assert result.exit_code != 0
    assert "Aborted" in result.output


def test_execute_without_credentials_has_friendly_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GOOGLE_CALENDAR_CREDENTIALS_FILE", str(tmp_path / "missing.json"))
    monkeypatch.setenv("GOOGLE_CALENDAR_TOKEN_FILE", str(tmp_path / "missing-token.json"))
    result = runner.invoke(
        app,
        [
            "calendar",
            "calibrate",
            "--pattern",
            "duration-scale",
            "--start-date",
            "2026-08-10",
            "--run-id",
            "missing-creds",
            "--output",
            str(tmp_path / "out"),
            "--execute",
            "--yes",
        ],
    )
    assert result.exit_code == 1
    assert "credentials were not found" in result.output


def test_cleanup_is_dry_run_without_api() -> None:
    result = runner.invoke(
        app,
        [
            "calendar",
            "cleanup",
            "--animation-id",
            "calibration-overlap-columns",
            "--run-id",
            "test-run",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "Execution: DRY RUN" in result.output
    assert "No deletion was performed" in result.output


def test_authenticated_cleanup_preview_reads_matches_but_never_deletes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gateway = FakeCalendarGateway()
    store = CalendarConfigStore(tmp_path / "calendar-config.json")
    service = CalibrationService(gateway, LabCalendarService(gateway, store))
    plan = build_calibration_plan("duration-scale", date(2026, 8, 10), run_id="preview-run")
    service.execute(plan)
    token = tmp_path / "token.json"
    token.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("GOOGLE_CALENDAR_TOKEN_FILE", str(token))
    monkeypatch.setattr(calendar_commands, "_google_gateway", lambda: gateway)
    monkeypatch.setattr(calendar_commands, "CalendarConfigStore", lambda: store)
    result = runner.invoke(
        app,
        [
            "calendar",
            "cleanup",
            "--animation-id",
            plan.animation_id,
            "--run-id",
            plan.run_id,
        ],
    )
    assert result.exit_code == 0, result.output
    assert "Matching events: 7 (authenticated metadata lookup)" in result.output
    assert gateway.delete_event_calls == 0


def test_record_calibration_writes_human_editable_yaml(tmp_path: Path) -> None:
    path = tmp_path / "observations.yaml"
    result = runner.invoke(
        app,
        [
            "calendar",
            "record-calibration",
            "--run-id",
            "observed-run",
            "--minimum-event-minutes",
            "15",
            "--usable-overlap-columns",
            "5",
            "--output",
            str(path),
        ],
    )
    assert result.exit_code == 0, result.output
    content = path.read_text(encoding="utf-8")
    assert "minimum_event_minutes: 15" in content
    assert "usable_overlap_columns: 5" in content
