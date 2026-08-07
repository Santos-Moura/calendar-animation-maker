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
