from datetime import date
from pathlib import Path

import pytest
from PIL import Image

from calendar_anim.calendar.calibration.artifacts import (
    build_report,
    write_dry_run_artifacts,
)
from calendar_anim.calendar.calibration.models import (
    CalibrationExecutionResult,
    CalibrationPlan,
)
from calendar_anim.calendar.calibration.patterns import build_calibration_plan

pytestmark = pytest.mark.integration


def test_calibration_artifacts_are_serializable_and_deterministic(tmp_path: Path) -> None:
    plan = build_calibration_plan("overlap-columns", date(2026, 8, 10), run_id="artifact-run")
    write_dry_run_artifacts(plan, tmp_path)
    loaded = CalibrationPlan.model_validate_json(
        (tmp_path / "calibration-plan.json").read_text(encoding="utf-8")
    )
    assert loaded == plan
    report = (tmp_path / "calibration-report.txt").read_text(encoding="utf-8")
    assert report.startswith("Overlap Columns Calibration")
    assert "Execution: DRY RUN" in report
    assert "overlap-6\n  Time: 14:00-14:45\n  Events: 6" in report
    assert "Target viewport: 1920x1080" in report
    assert "Observed results" in report
    assert "Group 6:\n- visually separated:" in report
    assert "Decision priority: visual separation" in report
    assert "logical expectation only" in report
    with Image.open(tmp_path / "expected-layout.png") as image:
        assert image.size == (1400, 900)
    execution = CalibrationExecutionResult.model_validate_json(
        (tmp_path / "execution-result.json").read_text(encoding="utf-8")
    )
    assert execution.executed is False


def test_human_report_records_duration_values() -> None:
    plan = build_calibration_plan("duration-scale", date(2026, 8, 10), run_id="report-run")
    report = build_report(plan, executed=False)
    assert "5m, 10m, 15m, 20m, 30m, 45m, 60m" in report


@pytest.mark.parametrize(
    ("pattern", "heading", "expected"),
    [
        (
            "color-palette",
            "Color Palette Calibration",
            "Logical name: lavender",
        ),
        (
            "position-grid",
            "Position Grid Calibration",
            "M-AM: day=monday, row=0",
        ),
        (
            "horizontal-bars",
            "Horizontal Bars Calibration",
            "bar-6\n  Time: 14:00-14:45\n  Cells: 6",
        ),
    ],
)
def test_remaining_patterns_have_specific_reports_and_previews(
    tmp_path: Path, pattern: str, heading: str, expected: str
) -> None:
    plan = build_calibration_plan(pattern, date(2026, 8, 17), run_id=f"artifact-{pattern}")  # type: ignore[arg-type]
    write_dry_run_artifacts(plan, tmp_path)
    report = (tmp_path / "calibration-report.txt").read_text(encoding="utf-8")
    assert report.startswith(heading)
    assert expected in report
    with Image.open(tmp_path / "expected-layout.png") as image:
        assert image.size == (1400, 900)


def test_horizontal_bar_report_has_a_manual_checklist_for_each_width(tmp_path: Path) -> None:
    plan = build_calibration_plan("horizontal-bars", date(2026, 8, 31), run_id="bar-checklist")
    write_dry_run_artifacts(plan, tmp_path)
    report = (tmp_path / "calibration-report.txt").read_text(encoding="utf-8")
    assert report.count("Visually contiguous:") == 6
    assert report.count("Visible gaps:") == 6
    assert "Expected logical width: 6" in report
