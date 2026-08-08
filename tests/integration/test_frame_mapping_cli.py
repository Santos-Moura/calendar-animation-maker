import json
from pathlib import Path

import pytest
from PIL import Image
from typer.testing import CliRunner

import calendar_anim.calendar.commands as calendar_commands
from calendar_anim.calendar.calibration.models import CalibrationProfile
from calendar_anim.calendar.calibration.profile import save_profile
from calendar_anim.calendar.fake import FakeCalendarGateway
from calendar_anim.cli import app
from calendar_anim.renderer.manifest import write_manifest
from tests.factories import make_manifest, make_ready_calibration_profile

pytestmark = pytest.mark.integration
runner = CliRunner()


def _mapping_inputs(tmp_path: Path, profile: CalibrationProfile | None = None) -> tuple[Path, Path]:
    manifest = make_manifest()
    frames = tmp_path / "frames"
    frames.mkdir()
    Image.new("RGB", (4, 4), "#808080").save(frames / "frame_000.png")
    manifest_path = tmp_path / "animation.json"
    write_manifest(manifest, manifest_path)
    profile_path = tmp_path / "profile.yaml"
    save_profile(profile or make_ready_calibration_profile(), profile_path)
    return manifest_path, profile_path


def test_map_frame_dry_run_is_local_and_writes_all_artifacts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest, profile = _mapping_inputs(tmp_path)
    output = tmp_path / "mapping"
    monkeypatch.setattr(
        calendar_commands,
        "_google_gateway",
        lambda: pytest.fail("dry-run must not create an API gateway"),
    )
    result = runner.invoke(
        app,
        [
            "calendar",
            "map-frame",
            str(manifest),
            "--frame",
            "0",
            "--profile",
            str(profile),
            "--start-date",
            "2026-09-07",
            "--run-id",
            "dry-frame",
            "--output",
            str(output),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "Week start: 2026-09-06" in result.output
    assert "Source grid: 4x4" in result.output
    assert "Target grid: 42x24" in result.output
    assert "Execution: DRY RUN" in result.output
    assert {path.name for path in output.iterdir()} == {
        "frame-plan.json",
        "mapping-report.txt",
        "source-frame.png",
        "mapped-preview.png",
        "mapped-debug.png",
        "execution-result.json",
    }
    assert "Mapping mode: sparse" in result.output


def test_map_frame_full_grid_and_background_flag_are_fully_local(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest, profile = _mapping_inputs(tmp_path)
    output = tmp_path / "full-grid"
    monkeypatch.setattr(
        calendar_commands,
        "_google_gateway",
        lambda: pytest.fail("full-grid dry-run must not create an API gateway"),
    )
    result = runner.invoke(
        app,
        [
            "calendar",
            "map-frame",
            str(manifest),
            "--profile",
            str(profile),
            "--start-date",
            "2026-09-07",
            "--mapping-mode",
            "full-grid",
            "--calendar-background-color-id",
            "5",
            "--run-id",
            "full-grid-dry",
            "--output",
            str(output),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "Mapping mode: full-grid" in result.output
    assert "Background structural cells:" in result.output
    assert "Calendar events: 1008 / 1200" in result.output
    assert "Background colorId: 5" in result.output
    plan = json.loads((output / "frame-plan.json").read_text(encoding="utf-8"))
    assert plan["mapping_mode"] == "full-grid"
    assert plan["background_color_id"] == "5"
    assert plan["statistics"]["calendar_events"] == 1008


def test_map_frame_rejects_invalid_mode_and_background_color(tmp_path: Path) -> None:
    manifest, profile = _mapping_inputs(tmp_path)
    invalid_mode = runner.invoke(
        app,
        [
            "calendar",
            "map-frame",
            str(manifest),
            "--profile",
            str(profile),
            "--mapping-mode",
            "filled-sometimes",
        ],
    )
    assert invalid_mode.exit_code != 0
    assert "Invalid value for '--mapping-mode'" in invalid_mode.output

    invalid_background = runner.invoke(
        app,
        [
            "calendar",
            "map-frame",
            str(manifest),
            "--profile",
            str(profile),
            "--mapping-mode",
            "full-grid",
            "--calendar-background-color-id",
            "99",
        ],
    )
    assert invalid_background.exit_code == 1
    assert "Unsupported Calendar background color ID" in invalid_background.output


def test_map_frame_help_lists_both_mapping_modes_and_background_option() -> None:
    result = runner.invoke(app, ["calendar", "map-frame", "--help"])
    assert result.exit_code == 0
    assert "--mapping-mode" in result.output
    assert "sparse" in result.output
    assert "full-grid" in result.output
    assert "Calendar colorId used" in result.output


def test_map_frame_reports_invalid_frame_range(tmp_path: Path) -> None:
    manifest, profile = _mapping_inputs(tmp_path)
    result = runner.invoke(
        app,
        [
            "calendar",
            "map-frame",
            str(manifest),
            "--frame",
            "12",
            "--profile",
            str(profile),
        ],
    )
    assert result.exit_code == 1
    assert "Manifest contains 1 frames (0-0)" in result.output


def test_incomplete_horizontal_calibration_allows_dry_run_but_blocks_execute(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    incomplete = make_ready_calibration_profile()
    incomplete.horizontal_bar_mapping.independent_cells_appear_contiguous = None
    incomplete.horizontal_bar_mapping.recommended_horizontal_strategy = None
    incomplete = CalibrationProfile.model_validate(incomplete.model_dump())
    manifest, profile = _mapping_inputs(tmp_path, incomplete)
    monkeypatch.setattr(
        calendar_commands,
        "_google_gateway",
        lambda: pytest.fail("readiness must block before API gateway construction"),
    )
    dry = runner.invoke(
        app,
        [
            "calendar",
            "map-frame",
            str(manifest),
            "--profile",
            str(profile),
            "--output",
            str(tmp_path / "dry"),
        ],
    )
    assert dry.exit_code == 0, dry.output
    assert "Mapper readiness: NOT READY" in dry.output
    real = runner.invoke(
        app,
        [
            "calendar",
            "map-frame",
            str(manifest),
            "--profile",
            str(profile),
            "--start-date",
            "2026-09-07",
            "--run-id",
            "blocked",
            "--output",
            str(tmp_path / "blocked"),
            "--execute",
            "--yes",
        ],
    )
    assert real.exit_code == 1
    assert "missing: horizontal-bars calibration" in real.output


def test_pending_subcolumn_order_allows_dry_run_but_blocks_execute(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    profile_data = make_ready_calibration_profile().model_dump()
    profile_data["subcolumn_order_mapping"] = {}
    incomplete = CalibrationProfile.model_validate(profile_data)
    manifest, profile = _mapping_inputs(tmp_path, incomplete)
    monkeypatch.setattr(
        calendar_commands,
        "_google_gateway",
        lambda: pytest.fail("slot readiness must block before API gateway construction"),
    )

    dry = runner.invoke(
        app,
        [
            "calendar",
            "map-frame",
            str(manifest),
            "--profile",
            str(profile),
            "--mapping-mode",
            "full-grid",
            "--output",
            str(tmp_path / "slot-dry"),
        ],
    )
    assert dry.exit_code == 0, dry.output
    assert "Mapper readiness: NOT READY" in dry.output
    assert "subcolumn-order calibration" in dry.output

    real = runner.invoke(
        app,
        [
            "calendar",
            "map-frame",
            str(manifest),
            "--profile",
            str(profile),
            "--mapping-mode",
            "full-grid",
            "--start-date",
            "2026-09-07",
            "--run-id",
            "slot-blocked",
            "--output",
            str(tmp_path / "slot-blocked"),
            "--execute",
            "--yes",
        ],
    )
    assert real.exit_code == 1
    assert "missing: subcolumn-order calibration" in real.output


def test_execute_requires_explicit_date_and_confirmation(tmp_path: Path) -> None:
    manifest, profile = _mapping_inputs(tmp_path)
    missing_date = runner.invoke(
        app,
        [
            "calendar",
            "map-frame",
            str(manifest),
            "--profile",
            str(profile),
            "--execute",
        ],
    )
    assert missing_date.exit_code == 1
    assert "--start-date is required" in missing_date.output
    aborted = runner.invoke(
        app,
        [
            "calendar",
            "map-frame",
            str(manifest),
            "--profile",
            str(profile),
            "--start-date",
            "2026-09-07",
            "--execute",
        ],
        input="n\n",
    )
    assert aborted.exit_code != 0
    assert "Continue?" in aborted.output


def test_full_grid_confirmation_discloses_cost_and_limit_blocks_before_api(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest, profile = _mapping_inputs(tmp_path)
    monkeypatch.setattr(
        calendar_commands,
        "_google_gateway",
        lambda: pytest.fail("guard must run before API gateway construction"),
    )
    aborted = runner.invoke(
        app,
        [
            "calendar",
            "map-frame",
            str(manifest),
            "--profile",
            str(profile),
            "--start-date",
            "2026-09-07",
            "--mapping-mode",
            "full-grid",
            "--run-id",
            "confirm-full-grid",
            "--output",
            str(tmp_path / "confirm"),
            "--execute",
        ],
        input="n\n",
    )
    assert aborted.exit_code != 0
    assert "Mapping mode: FULL-GRID" in aborted.output
    assert "Foreground events:" in aborted.output
    assert "Background events:" in aborted.output
    assert "This will create 1008 real Google Calendar events" in aborted.output

    blocked = runner.invoke(
        app,
        [
            "calendar",
            "map-frame",
            str(manifest),
            "--profile",
            str(profile),
            "--start-date",
            "2026-09-07",
            "--mapping-mode",
            "full-grid",
            "--max-events",
            "1000",
            "--run-id",
            "limited-full-grid",
            "--output",
            str(tmp_path / "limited-full-grid"),
            "--execute",
            "--yes",
        ],
    )
    assert blocked.exit_code == 1
    assert "above the configured execute limit" in blocked.output


def test_execute_uses_fake_gateway_and_limit_blocks_before_api(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest, profile = _mapping_inputs(tmp_path)
    gateway = FakeCalendarGateway()
    monkeypatch.setattr(calendar_commands, "_google_gateway", lambda: gateway)
    blocked = runner.invoke(
        app,
        [
            "calendar",
            "map-frame",
            str(manifest),
            "--profile",
            str(profile),
            "--start-date",
            "2026-09-07",
            "--run-id",
            "limited",
            "--max-events",
            "1",
            "--output",
            str(tmp_path / "limited"),
            "--execute",
            "--yes",
        ],
    )
    assert blocked.exit_code == 1
    assert "above the configured execute limit" in blocked.output
    assert gateway.create_calendar_calls == 0

    executed = runner.invoke(
        app,
        [
            "calendar",
            "map-frame",
            str(manifest),
            "--profile",
            str(profile),
            "--start-date",
            "2026-09-07",
            "--run-id",
            "executed",
            "--output",
            str(tmp_path / "executed"),
            "--execute",
            "--yes",
        ],
    )
    assert executed.exit_code == 0, executed.output
    assert "Created events:" in executed.output
    assert gateway.create_calendar_calls == 1
