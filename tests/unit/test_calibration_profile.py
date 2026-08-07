from pathlib import Path

import pytest
import yaml

from calendar_anim.calendar.calibration.models import (
    CalibrationObservations,
    CalibrationProfile,
)
from calendar_anim.calendar.calibration.profile import (
    apply_observations,
    load_observations,
    load_profile,
    profile_summary,
    save_profile,
)

pytestmark = pytest.mark.unit


def test_profile_derives_rows_and_columns_from_recorded_measurements() -> None:
    observations = CalibrationObservations(
        run_id="measured-run",
        pattern="overlap-columns",
        calendar_ui={
            "browser_zoom_percent": 100,
            "viewport_width": 1920,
            "viewport_height": 1080,
            "visible_start_hour": 6,
            "visible_end_hour": 18,
        },
        observations={
            "minimum_visible_event_minutes": 5,
            "minimum_distinguishable_height_minutes": 30,
            "maximum_tested_overlap_columns": 6,
            "usable_overlap_columns": 5,
        },
    )
    profile = apply_observations(CalibrationProfile(), observations)
    assert profile.vertical_mapping.logical_rows == 24
    assert profile.horizontal_mapping.usable_overlap_columns_per_day == 5
    assert profile.horizontal_mapping.logical_columns == 35
    assert "Logical rows: 24" in profile_summary(profile)
    assert "Logical columns: 35" in profile_summary(profile)


def test_legacy_observation_yaml_still_loads(tmp_path: Path) -> None:
    path = tmp_path / "legacy.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "schema_version": "1.0",
                "run_id": "legacy-run",
                "pattern": "duration-scale",
                "calendar_ui": {"view": "week", "density": None},
                "observations": {"minimum_event_minutes": 15, "notes": "old file"},
            }
        ),
        encoding="utf-8",
    )
    observations = load_observations(path)
    assert observations.observations.minimum_visible_event_minutes == 15
    profile = apply_observations(CalibrationProfile(), observations)
    assert profile.vertical_mapping.minimum_visible_event_minutes == 15


def test_profile_round_trip_preserves_consolidated_values(tmp_path: Path) -> None:
    path = tmp_path / "profile.yaml"
    profile = CalibrationProfile()
    profile.horizontal_mapping.usable_overlap_columns_per_day = 4
    profile = CalibrationProfile.model_validate(profile.model_dump())
    save_profile(profile, path)
    assert load_profile(path) == profile


def test_old_profile_loads_with_new_sections_and_preserves_grid(tmp_path: Path) -> None:
    path = tmp_path / "old-profile.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "schema_version": "1.0",
                "calendar_ui": {"visible_start_hour": 6, "visible_end_hour": 18},
                "vertical_mapping": {
                    "minimum_visible_event_minutes": 5,
                    "minimum_distinguishable_height_minutes": 30,
                },
                "horizontal_mapping": {
                    "maximum_tested_overlap_columns": 6,
                    "usable_overlap_columns_per_day": 6,
                    "days_used": 7,
                },
            }
        ),
        encoding="utf-8",
    )
    profile = load_profile(path)
    assert profile.schema_version == "1.1"
    assert profile.candidate_grid.width == 42
    assert profile.candidate_grid.height == 24
    assert profile.color_mapping.preferred_color_ids == []
    assert profile.mapper_readiness == "NOT READY"


def test_new_observations_consolidate_without_erasing_existing_axes() -> None:
    profile = CalibrationProfile.model_validate(
        {
            "vertical_mapping": {
                "minimum_visible_event_minutes": 5,
                "minimum_distinguishable_height_minutes": 30,
            },
            "horizontal_mapping": {
                "maximum_tested_overlap_columns": 6,
                "usable_overlap_columns_per_day": 6,
            },
        }
    )
    color_observation = CalibrationObservations(
        run_id="colors",
        pattern="color-palette",
        observations={
            "tested_color_ids": ["1", "2", "3"],
            "preferred_color_ids": ["1", "3"],
            "recommended_color_count": 2,
            "poor_contrast_color_ids": ["2"],
            "similar_color_groups": [["1", "9"]],
            "notes": "Measured colors.",
        },
    )
    profile = apply_observations(profile, color_observation)
    assert profile.vertical_mapping.logical_rows == 24
    assert profile.horizontal_mapping.logical_columns == 42
    assert profile.color_mapping.preferred_color_ids == ["1", "3"]
    assert profile.color_mapping.recommended_color_count == 2
    assert profile.color_mapping.notes == "Measured colors."

    position_observation = CalibrationObservations(
        run_id="position",
        pattern="position-grid",
        observations={
            "week_alignment_ok": True,
            "timezone_alignment_ok": True,
            "day_alignment_ok": True,
            "vertical_alignment_ok": False,
            "week_starts_on": "monday",
        },
    )
    profile = apply_observations(profile, position_observation)
    assert profile.position_mapping.week_alignment_ok is True
    assert profile.position_mapping.vertical_alignment_ok is False
    assert profile.position_mapping.week_starts_on == "monday"

    bars_observation = CalibrationObservations(
        run_id="bars",
        pattern="horizontal-bars",
        observations={
            "independent_cells_appear_contiguous": True,
            "visible_gaps_between_cells": False,
            "same_color_cells_merge_visually": True,
            "maximum_useful_bar_width": 6,
            "partial_bar_positioning_predictable": None,
            "recommended_horizontal_strategy": "independent-cells",
        },
    )
    profile = apply_observations(profile, bars_observation)
    assert profile.horizontal_bar_mapping.maximum_useful_bar_width == 6
    assert profile.mapper_readiness == "READY FOR SINGLE-FRAME EXPERIMENT"
    summary = profile_summary(profile)
    assert "Preferred color IDs: 1, 3" in summary
    assert "Vertical alignment: NOT OK" in summary
    assert "Recommended strategy: independent-cells" in summary
    assert "Mapper readiness: READY FOR SINGLE-FRAME EXPERIMENT" in summary


def test_mapper_readiness_stays_not_ready_while_any_calibration_is_missing() -> None:
    profile = CalibrationProfile.model_validate(
        {
            "vertical_mapping": {
                "minimum_visible_event_minutes": 5,
                "minimum_distinguishable_height_minutes": 30,
            },
            "horizontal_mapping": {
                "maximum_tested_overlap_columns": 6,
                "usable_overlap_columns_per_day": 6,
            },
            "color_mapping": {
                "tested_color_ids": ["1"],
                "preferred_color_ids": ["1"],
                "recommended_color_count": 1,
            },
            "position_mapping": {
                "week_alignment_ok": True,
                "timezone_alignment_ok": True,
                "day_alignment_ok": True,
                "vertical_alignment_ok": True,
            },
        }
    )
    assert profile.mapper_readiness == "NOT READY"
    assert "Horizontal bar mapping" in profile_summary(profile)
