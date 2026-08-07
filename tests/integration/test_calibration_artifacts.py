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
    assert "Execution: DRY RUN" in report
    assert "overlap-6: 6 event(s)" in report
    assert "logical preview" in report
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
