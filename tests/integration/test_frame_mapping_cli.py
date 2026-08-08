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
        "execution-result.json",
    }


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
    assert "record horizontal-bars before upload" in real.output


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
