from datetime import date
from pathlib import Path

import pytest
import yaml
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
    assert "subcolumn-order" in result.output
    assert "vertical-compression" in result.output
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


def test_record_calibration_imports_completed_vertical_compression_yaml(
    tmp_path: Path,
) -> None:
    artifacts = tmp_path / "artifacts"
    dry_run = runner.invoke(
        app,
        [
            "calendar",
            "calibrate",
            "--pattern",
            "vertical-compression",
            "--start-date",
            "2026-11-15",
            "--run-id",
            "vertical-observed",
            "--output",
            str(artifacts),
        ],
    )
    assert dry_run.exit_code == 0, dry_run.output
    observations_path = artifacts / "calibration-observations.yaml"
    payload = yaml.safe_load(observations_path.read_text(encoding="utf-8"))
    vertical = payload["observations"]["vertical_compression"]
    vertical["control_vs_compressed"]["same_total_height"] = True
    vertical["control_vs_compressed"]["visually_equivalent"] = True
    vertical["fixed_start_mixed_duration"]["slot_order_preserved"] = True
    vertical["staggered"]["overlap_layout_stable"] = True
    vertical["conclusion"]["visually_acceptable"] = True
    vertical["conclusion"]["safe_for_mapper"] = False
    observations_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    profile = tmp_path / "profile.yaml"
    recorded = runner.invoke(
        app,
        [
            "calendar",
            "record-calibration",
            "--run-id",
            "vertical-observed",
            "--pattern",
            "vertical-compression",
            "--observations-file",
            str(observations_path),
            "--profile-output",
            str(profile),
        ],
    )
    assert recorded.exit_code == 0, recorded.output

    summary = runner.invoke(app, ["calendar", "calibration-summary", "--profile", str(profile)])
    assert summary.exit_code == 0, summary.output
    assert "Vertical event compression experiment" in summary.output
    assert "Status: recorded" in summary.output
    assert "Control/compressed same height: yes" in summary.output
    assert "Safe for production mapper: no" in summary.output


def test_record_calibration_rejects_mismatched_observation_identity(tmp_path: Path) -> None:
    observations = tmp_path / "observations.yaml"
    observations.write_text(
        "schema_version: '1.0'\nrun_id: other-run\npattern: vertical-compression\n",
        encoding="utf-8",
    )
    result = runner.invoke(
        app,
        [
            "calendar",
            "record-calibration",
            "--run-id",
            "requested-run",
            "--observations-file",
            str(observations),
        ],
    )
    assert result.exit_code == 1
    assert "does not match --run-id" in result.output


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
    assert "minimum_visible_event_minutes: 15" in content
    assert "usable_overlap_columns: 5" in content
    assert (tmp_path / "calibration-profile.yaml").is_file()


def test_record_and_summary_build_consolidated_logical_mapping(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    observations = tmp_path / "overlap-observations.yaml"
    profile = tmp_path / "profile.yaml"
    result = runner.invoke(
        app,
        [
            "calendar",
            "record-calibration",
            "--run-id",
            "overlap-observed",
            "--pattern",
            "overlap-columns",
            "--minimum-visible-event-minutes",
            "5",
            "--minimum-distinguishable-height-minutes",
            "30",
            "--maximum-tested-overlap-columns",
            "6",
            "--usable-overlap-columns",
            "5",
            "--browser-zoom",
            "100",
            "--viewport-width",
            "1920",
            "--viewport-height",
            "1080",
            "--titles-visible",
            "--colors-distinguishable",
            "--notes",
            "Five columns remained readable.",
            "--output",
            str(observations),
            "--profile-output",
            str(profile),
        ],
    )
    assert result.exit_code == 0, result.output
    profile_content = profile.read_text(encoding="utf-8")
    assert "logical_rows: 24" in profile_content
    assert "usable_overlap_columns_per_day: 5" in profile_content
    assert "logical_columns: 35" in profile_content

    monkeypatch.setattr(
        calendar_commands,
        "_google_gateway",
        lambda: pytest.fail("summary must not create an API gateway"),
    )
    summary = runner.invoke(
        app,
        ["calendar", "calibration-summary", "--profile", str(profile)],
    )
    assert summary.exit_code == 0, summary.output
    assert "Minimum visible event: 5 minutes" in summary.output
    assert "Minimum distinguishable height: 30 minutes" in summary.output
    assert "Logical rows: 24" in summary.output
    assert "Usable overlaps per day: 5" in summary.output
    assert "Logical columns: 35" in summary.output
    assert "Candidate logical grid: 35x24" in summary.output
    assert "no Calendar API call was made" in summary.output


def test_summary_without_measurements_shows_pending(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        ["calendar", "calibration-summary", "--profile", str(tmp_path / "missing.yaml")],
    )
    assert result.exit_code == 0, result.output
    assert "Minimum visible event: pending" in result.output
    assert "Usable overlaps per day: not measured yet" in result.output
    assert "Logical columns: pending overlap-columns calibration" in result.output
    assert "Tested color IDs: pending calibration" in result.output
    assert "Week alignment: pending calibration" in result.output
    assert "Recommended strategy: pending" in result.output
    assert "Mapper readiness: NOT READY" in result.output


def test_summary_can_overlay_observations_by_run_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    recorded = runner.invoke(
        app,
        [
            "calendar",
            "record-calibration",
            "--run-id",
            "summary-run",
            "--pattern",
            "overlap-columns",
            "--maximum-tested-overlap-columns",
            "6",
            "--usable-overlap-columns",
            "4",
        ],
    )
    assert recorded.exit_code == 0, recorded.output
    result = runner.invoke(
        app,
        ["calendar", "calibration-summary", "--run-id", "summary-run"],
    )
    assert result.exit_code == 0, result.output
    assert "Usable overlaps per day: 4" in result.output
    assert "Logical columns: 28" in result.output


@pytest.mark.parametrize(
    "pattern", ["color-palette", "position-grid", "horizontal-bars", "subcolumn-order"]
)
def test_remaining_calibration_dry_runs_never_create_an_api_gateway(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    pattern: str,
) -> None:
    monkeypatch.setattr(
        calendar_commands,
        "_google_gateway",
        lambda: pytest.fail("dry-run must not create an API gateway"),
    )
    result = runner.invoke(
        app,
        [
            "calendar",
            "calibrate",
            "--pattern",
            pattern,
            "--start-date",
            "2026-08-17",
            "--run-id",
            f"dry-{pattern}",
            "--output",
            str(tmp_path / pattern),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "Execution: DRY RUN" in result.output


def test_cli_records_all_remaining_calibrations_and_becomes_mapper_ready(
    tmp_path: Path,
) -> None:
    profile = tmp_path / "profile.yaml"

    base = runner.invoke(
        app,
        [
            "calendar",
            "record-calibration",
            "--run-id",
            "base-observation",
            "--pattern",
            "overlap-columns",
            "--minimum-visible-event-minutes",
            "5",
            "--minimum-distinguishable-height-minutes",
            "30",
            "--maximum-tested-overlap-columns",
            "6",
            "--usable-overlap-columns",
            "6",
            "--output",
            str(tmp_path / "base.yaml"),
            "--profile-output",
            str(profile),
        ],
    )
    assert base.exit_code == 0, base.output

    colors = runner.invoke(
        app,
        [
            "calendar",
            "record-calibration",
            "--run-id",
            "color-observation",
            "--pattern",
            "color-palette",
            "--preferred-color-ids",
            "1,5,7,9",
            "--recommended-color-count",
            "4",
            "--poor-contrast-color-ids",
            "8",
            "--similar-color-groups",
            "1,9;2,10",
            "--notes",
            "Measured palette.",
            "--output",
            str(tmp_path / "colors.yaml"),
            "--profile-output",
            str(profile),
        ],
    )
    assert colors.exit_code == 0, colors.output
    color_yaml = yaml.safe_load((tmp_path / "colors.yaml").read_text(encoding="utf-8"))
    assert color_yaml["observations"]["tested_color_ids"] == [str(value) for value in range(1, 12)]
    assert color_yaml["observations"]["preferred_color_ids"] == ["1", "5", "7", "9"]

    position = runner.invoke(
        app,
        [
            "calendar",
            "record-calibration",
            "--run-id",
            "position-observation",
            "--pattern",
            "position-grid",
            "--week-alignment-ok",
            "--timezone-alignment-ok",
            "--day-alignment-ok",
            "--vertical-alignment-ok",
            "--week-starts-on",
            "monday",
            "--output",
            str(tmp_path / "position.yaml"),
            "--profile-output",
            str(profile),
        ],
    )
    assert position.exit_code == 0, position.output

    bars = runner.invoke(
        app,
        [
            "calendar",
            "record-calibration",
            "--run-id",
            "bars-observation",
            "--pattern",
            "horizontal-bars",
            "--independent-cells-contiguous",
            "--no-visible-cell-gaps",
            "--same-color-cells-merge",
            "--maximum-useful-bar-width",
            "6",
            "--recommended-horizontal-strategy",
            "independent-cells",
            "--output",
            str(tmp_path / "bars.yaml"),
            "--profile-output",
            str(profile),
        ],
    )
    assert bars.exit_code == 0, bars.output

    pending_summary = runner.invoke(
        app,
        ["calendar", "calibration-summary", "--profile", str(profile)],
    )
    assert pending_summary.exit_code == 0, pending_summary.output
    assert "Mapper readiness: NOT READY" in pending_summary.output
    assert "- subcolumn-order calibration" in pending_summary.output

    slots = runner.invoke(
        app,
        [
            "calendar",
            "record-calibration",
            "--run-id",
            "slot-order-observation",
            "--pattern",
            "subcolumn-order",
            "--visual-order-forward",
            "0,1,2,3,4,5",
            "--visual-order-reverse",
            "5,4,3,2,1,0",
            "--visual-order-shuffled",
            "2,5,0,4,1,3",
            "--stable-after-refresh",
            "--stable-after-navigation",
            "--stable-after-reopen",
            "--creation-order-controls-layout",
            "--recommended-slot-order-strategy",
            "creation-order",
            "--notes",
            "Creation order remained stable.",
            "--output",
            str(tmp_path / "slots.yaml"),
            "--profile-output",
            str(profile),
        ],
    )
    assert slots.exit_code == 0, slots.output
    slot_yaml = yaml.safe_load((tmp_path / "slots.yaml").read_text(encoding="utf-8"))
    assert slot_yaml["observations"]["visual_order_forward"] == [0, 1, 2, 3, 4, 5]
    assert slot_yaml["observations"]["stable_after_refresh"] is True

    summary = runner.invoke(
        app,
        ["calendar", "calibration-summary", "--profile", str(profile)],
    )
    assert summary.exit_code == 0, summary.output
    assert "Preferred color IDs: 1, 5, 7, 9" in summary.output
    assert "Week alignment: OK" in summary.output
    assert "Recommended strategy: independent-cells" in summary.output
    assert "Subcolumn order mapping\n  Status: recorded" in summary.output
    assert "Recommended slot strategy: creation-order" in summary.output
    assert "Candidate logical grid: 42x24" in summary.output
    assert "Mapper readiness: READY FOR SINGLE-FRAME EXPERIMENT" in summary.output


def test_record_subcolumn_order_rejects_invalid_or_incomplete_permutations(
    tmp_path: Path,
) -> None:
    result = runner.invoke(
        app,
        [
            "calendar",
            "record-calibration",
            "--run-id",
            "invalid-slots",
            "--pattern",
            "subcolumn-order",
            "--visual-order-forward",
            "0,1,2,3,4,6",
            "--output",
            str(tmp_path / "invalid.yaml"),
        ],
    )

    assert result.exit_code == 1
    assert "must contain each slot index from 0 to 5 exactly once" in result.output
    assert not (tmp_path / "invalid.yaml").exists()


def test_cli_records_summary_ordering_evidence_without_experimental_pattern(
    tmp_path: Path,
) -> None:
    observations = tmp_path / "summary-ordering.yaml"
    profile = tmp_path / "profile.yaml"
    result = runner.invoke(
        app,
        [
            "calendar",
            "record-calibration",
            "--run-id",
            "summary-ordering-evidence",
            "--pattern",
            "subcolumn-order",
            "--ordering-factor-tested",
            "--ordering-controlling-property",
            "summary",
            "--ordering-factor-stable",
            "--recommended-slot-order-strategy",
            "summary-prefix",
            "--output",
            str(observations),
            "--profile-output",
            str(profile),
        ],
    )

    assert result.exit_code == 0, result.output
    recorded = yaml.safe_load(profile.read_text(encoding="utf-8"))
    mapping = recorded["subcolumn_order_mapping"]
    assert mapping["status"] == "recorded"
    assert mapping["factor_tested"] is True
    assert mapping["controlling_property"] == "summary"
    assert mapping["factor_stable"] is True
    assert mapping["recommended_slot_order_strategy"] == "summary-prefix"
