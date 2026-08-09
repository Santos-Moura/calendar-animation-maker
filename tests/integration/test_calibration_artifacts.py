from datetime import date
from pathlib import Path

import pytest
import yaml
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
        (
            "subcolumn-order",
            "Subcolumn Order Calibration",
            "Creation order: S5 S4 S3 S2 S1 S0",
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


def test_subcolumn_order_report_has_manual_stability_checklist(tmp_path: Path) -> None:
    plan = build_calibration_plan(
        "subcolumn-order", date(2026, 9, 7), run_id="slot-order-checklist"
    )
    write_dry_run_artifacts(plan, tmp_path)
    report = (tmp_path / "calibration-report.txt").read_text(encoding="utf-8")

    assert "Creation order: S0 S1 S2 S3 S4 S5" in report
    assert "Creation order: S5 S4 S3 S2 S1 S0" in report
    assert "Creation order: S2 S5 S0 S4 S1 S3" in report
    assert "After browser refresh:" in report
    assert "After navigating away and back:" in report
    assert "After reopening Calendar:" in report
    assert "not a guarantee of Google Calendar visual ordering" in report


def test_subcolumn_order_serialization_preserves_creation_sequence(tmp_path: Path) -> None:
    plan = build_calibration_plan(
        "subcolumn-order", date(2026, 9, 7), run_id="slot-order-serialized"
    )
    write_dry_run_artifacts(plan, tmp_path)

    loaded = CalibrationPlan.model_validate_json(
        (tmp_path / "calibration-plan.json").read_text(encoding="utf-8")
    )

    assert [event.summary for event in loaded.events] == [event.summary for event in plan.events]
    assert [event.private_metadata["creation_sequence"] for event in loaded.events] == [
        event.private_metadata["creation_sequence"] for event in plan.events
    ]
    assert [event.private_metadata["subcolumn_index"] for event in loaded.events] == [
        event.private_metadata["subcolumn_index"] for event in plan.events
    ]


def test_vertical_compression_writes_specific_report_preview_and_observation_template(
    tmp_path: Path,
) -> None:
    plan = build_calibration_plan(
        "vertical-compression", date(2026, 11, 15), run_id="vertical-artifacts"
    )
    write_dry_run_artifacts(plan, tmp_path)

    report = (tmp_path / "calibration-report.txt").read_text(encoding="utf-8")
    assert report.startswith("Vertical Event Compression Calibration")
    assert "CONTROL\n-------\nevents: 12" in report
    assert "COMPRESSED\n----------\nevents: 6" in report
    assert "MIXED LENGTH\n------------\nevents: 6" in report
    assert "STAGGERED\n---------\nevents: 6" in report
    assert "Compressed columns keep 00..05 order: yes/no" in report
    assert "No result is inferred by this dry-run." in report

    observations = yaml.safe_load(
        (tmp_path / "calibration-observations.yaml").read_text(encoding="utf-8")
    )
    vertical = observations["observations"]["vertical_compression"]
    assert vertical["control_vs_compressed"]["same_total_height"] is None
    assert vertical["fixed_start_mixed_duration"]["slot_order_preserved"] is None
    assert vertical["staggered"]["overlap_layout_stable"] is None
    assert vertical["conclusion"]["safe_for_mapper"] is None

    with Image.open(tmp_path / "expected-layout.png") as image:
        assert image.size == (1400, 900)


def test_synchronized_bands_write_report_preview_and_observation_template(
    tmp_path: Path,
) -> None:
    plan = build_calibration_plan(
        "synchronized-horizontal-bands",
        date(2026, 11, 22),
        run_id="synchronized-band-artifacts",
    )
    write_dry_run_artifacts(plan, tmp_path)

    report = (tmp_path / "calibration-report.txt").read_text(encoding="utf-8")
    assert report.startswith("Synchronized Horizontal Bands Calibration")
    assert "band-uniform-long\n  Time: 06:00-08:00\n  Events: 6" in report
    assert "band-foreground-heavy\n  Time: 13:00-18:00\n  Events: 6" in report
    assert "Every group contains six events with identical starts and ends." in report
    assert "All bands keep equal widths: yes/no" in report

    observations = yaml.safe_load(
        (tmp_path / "calibration-observations.yaml").read_text(encoding="utf-8")
    )
    synchronized = observations["observations"]["synchronized_horizontal_bands"]
    assert synchronized["equal_widths_preserved"] is None
    assert synchronized["slot_order_preserved"] is None
    assert synchronized["safe_for_mapper"] is None

    with Image.open(tmp_path / "expected-layout.png") as image:
        assert image.size == (1400, 900)
