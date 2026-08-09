import json
from pathlib import Path

import pytest
from PIL import Image
from typer.testing import CliRunner

import calendar_anim.calendar.commands as calendar_commands
from calendar_anim.calendar.calibration.profile import save_profile
from calendar_anim.cli import app
from calendar_anim.renderer.manifest import write_manifest
from tests.factories import make_manifest, make_ready_calibration_profile

pytestmark = pytest.mark.integration
runner = CliRunner()


def _inputs(tmp_path: Path) -> tuple[Path, Path]:
    manifest = make_manifest()
    frames = tmp_path / "frames"
    frames.mkdir()
    Image.new("RGB", (4, 4), "#000000").save(frames / "frame_000.png")
    manifest_path = tmp_path / "animation.json"
    write_manifest(manifest, manifest_path)
    profile_path = tmp_path / "profile.yaml"
    save_profile(make_ready_calibration_profile(), profile_path)
    return manifest_path, profile_path


def test_estimate_band_compression_is_local_and_writes_artifacts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest, profile = _inputs(tmp_path)
    output = tmp_path / "band-estimate"
    monkeypatch.setattr(
        calendar_commands,
        "_google_gateway",
        lambda: pytest.fail("band estimator must never create an API gateway"),
    )

    result = runner.invoke(
        app,
        [
            "calendar",
            "estimate-band-compression",
            str(manifest),
            "--profile",
            str(profile),
            "--output",
            str(output),
        ],
    )

    assert result.exit_code == 0, result.output
    assert "Synchronized Horizontal-Band Compression Estimate" in result.output
    assert "Grid: 42x24" in result.output
    assert "Columns per day: 6" in result.output
    assert "Baseline: 1008" in result.output
    assert "No authentication or Calendar API call was made." in result.output
    payload = json.loads(
        (output / "horizontal-band-compression-estimate.json").read_text(encoding="utf-8")
    )
    assert payload["total_baseline_events"] == 1008
    assert payload["total_compressed_events"] % 6 == 0
    assert payload["total_compressed_events"] < payload["total_baseline_events"]
    assert (output / "horizontal-band-compression-estimate.txt").is_file()


def test_estimate_band_compression_rejects_invalid_manifest(tmp_path: Path) -> None:
    invalid = tmp_path / "invalid.json"
    invalid.write_text("not json", encoding="utf-8")

    result = runner.invoke(app, ["calendar", "estimate-band-compression", str(invalid)])

    assert result.exit_code == 1
    assert "Error:" in result.output
