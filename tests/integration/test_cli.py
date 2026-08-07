from pathlib import Path

import pytest
from typer.testing import CliRunner

from calendar_anim.cli import app

runner = CliRunner()
pytestmark = pytest.mark.integration


def test_cli_inspect(tiny_video: Path) -> None:
    result = runner.invoke(app, ["inspect", str(tiny_video)])
    assert result.exit_code == 0, result.output
    assert "Dimensions: 16x12" in result.output


def test_cli_render_estimate_and_validate(tiny_video: Path, tmp_path: Path) -> None:
    output = tmp_path / "demo"
    result = runner.invoke(
        app,
        [
            "render",
            str(tiny_video),
            "--frames",
            "3",
            "--width",
            "8",
            "--height",
            "6",
            "--palette",
            "grayscale",
            "--colors",
            "2",
            "--output",
            str(output),
        ],
    )
    assert result.exit_code == 0, result.output
    assert (output / "preview.gif").is_file()
    assert len(list((output / "frames").glob("*.png"))) == 3
    manifest = output / "animation.json"
    assert runner.invoke(app, ["validate", str(manifest)]).exit_code == 0
    estimate = runner.invoke(app, ["estimate", str(manifest)])
    assert estimate.exit_code == 0
    assert "Weeks used: 3" in estimate.output


def test_cli_calendar_plan(tiny_video: Path, tmp_path: Path) -> None:
    output = tmp_path / "demo"
    rendered = runner.invoke(app, ["render", str(tiny_video), "-o", str(output), "--frames", "1"])
    assert rendered.exit_code == 0, rendered.output
    plan = tmp_path / "plan.json"
    result = runner.invoke(
        app,
        [
            "calendar",
            "plan",
            str(output / "animation.json"),
            "--start-date",
            "2026-08-10",
            "--timezone",
            "America/Sao_Paulo",
            "-o",
            str(plan),
        ],
    )
    assert result.exit_code == 0, result.output
    assert plan.is_file()
