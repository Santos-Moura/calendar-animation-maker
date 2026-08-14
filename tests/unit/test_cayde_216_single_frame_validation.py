from datetime import date, timedelta
from pathlib import Path

import pytest
from PIL import Image

from calendar_anim.calendar.calibration.models import CalibrationProfile
from calendar_anim.calendar.cayde_216.planner import FIRST_WEEK, FRAME_COUNT, RUN_ID
from calendar_anim.calendar.cayde_216.single_frame_validation import (
    CAPTURE_MODE,
    CAPTURE_RESOLUTION,
    CAPTURE_ZOOM_PERCENT,
    FRAME_INDEX,
    HUMAN_FRAME,
    SOURCE_RUN_ID_168,
    VALIDATION_RUN_ID,
    VALIDATION_RUN_ID_168,
    VALIDATION_WEEK,
    VALIDATION_WEEK_168,
    ensure_bulk_hashes_unchanged,
    remote_audit_result,
    select_cleanup_ids,
    validation_expansion_metrics,
    validation_preflight_result,
)
from calendar_anim.calendar.high_detail import apply_high_detail_grid
from calendar_anim.calendar.hybrid_capture.models import HybridOutputMode
from calendar_anim.calendar.hybrid_capture.service import (
    image_has_expected_visual_occupancy,
)
from calendar_anim.calendar.recurrence_compaction.models import (
    RecurrenceMigrationPlan,
    RecurringParentPlan,
)
from calendar_anim.calendar.recurrence_compaction.planner import _parent_id
from calendar_anim.exceptions import CalendarAnimError


def test_validation_frame_week_and_namespace_are_isolated() -> None:
    bulk_last_week = FIRST_WEEK + timedelta(weeks=FRAME_COUNT - 1)

    assert HUMAN_FRAME == 93
    assert FRAME_INDEX == 92
    assert date(2034, 6, 25) == VALIDATION_WEEK
    assert bulk_last_week + timedelta(weeks=1) == VALIDATION_WEEK
    assert VALIDATION_RUN_ID != RUN_ID

    occurrence_keys = ["f0092:caone", "f0092:catwo"]
    validation_id = _parent_id(VALIDATION_RUN_ID, "signature", "group", 0, occurrence_keys)
    bulk_id = _parent_id(RUN_ID, "signature", "group", 0, occurrence_keys)
    assert validation_id != bulk_id


def test_168_validation_geometry_and_namespace_are_isolated() -> None:
    profile = apply_high_detail_grid(CalibrationProfile(), "168x96")

    assert len(VALIDATION_RUN_ID_168) <= 64
    assert VALIDATION_RUN_ID_168 != VALIDATION_RUN_ID
    assert SOURCE_RUN_ID_168 != RUN_ID
    assert VALIDATION_WEEK + timedelta(weeks=1) == VALIDATION_WEEK_168
    assert profile.candidate_grid.width == 168
    assert profile.candidate_grid.height == 96
    assert profile.horizontal_mapping.usable_overlap_columns_per_day == 24
    assert profile.vertical_mapping.minimum_distinguishable_height_minutes == 11.25

    keys = ["f0092:caone"]
    assert _parent_id(VALIDATION_RUN_ID_168, "signature", "group", 0, keys) != _parent_id(
        RUN_ID, "signature", "group", 0, keys
    )


def test_validation_conflict_gate_stops_on_conflict_or_bulk_mutation() -> None:
    assert validation_preflight_result([], True) == "PASS"
    assert validation_preflight_result(["existing"], True) == "STOP"
    assert validation_preflight_result([], False) == "STOP"
    ensure_bulk_hashes_unchanged({"bulk": "same"}, {"bulk": "same"})
    with pytest.raises(CalendarAnimError, match="Bulk checkpoint"):
        ensure_bulk_hashes_unchanged({"bulk": "before"}, {"bulk": "after"})


def test_validation_remote_audit_requires_exact_equality() -> None:
    assert (
        remote_audit_result(
            missing=0,
            extra=0,
            duplicates=0,
            wrong_time=0,
            wrong_summary=0,
            wrong_color=0,
        )
        == "PASS"
    )
    assert (
        remote_audit_result(
            missing=0,
            extra=0,
            duplicates=0,
            wrong_time=0,
            wrong_summary=0,
            wrong_color=1,
        )
        == "FAIL"
    )
    assert (
        remote_audit_result(
            missing=0,
            extra=0,
            duplicates=0,
            wrong_time=0,
            wrong_summary=0,
            wrong_color=0,
            parent_get_missing=1,
        )
        == "FAIL"
    )


def test_validation_expansion_is_exact_and_detects_duplicates() -> None:
    exact = RecurrenceMigrationPlan.model_construct(
        parents=[RecurringParentPlan.model_construct(occurrence_keys=["a", "b"])]
    )
    duplicate = RecurrenceMigrationPlan.model_construct(
        parents=[RecurringParentPlan.model_construct(occurrence_keys=["a", "a", "b"])]
    )

    assert validation_expansion_metrics({"a", "b"}, exact) == {
        "missing": 0,
        "extra": 0,
        "duplicates": 0,
        "exact": True,
    }
    assert validation_expansion_metrics({"a", "b"}, duplicate)["exact"] is False


def test_validation_capture_uses_final_pipeline_and_rejects_empty_expected_image(
    tmp_path: Path,
) -> None:
    cyan = tmp_path / "cyan.png"
    empty = tmp_path / "empty.png"
    Image.new("RGB", (126, 72), (3, 155, 229)).save(cyan)
    Image.new("RGB", (126, 72), (255, 255, 255)).save(empty)

    assert CAPTURE_MODE is HybridOutputMode.HEADER_PRESERVED_FILL
    assert CAPTURE_RESOLUTION == (1512, 864)
    assert CAPTURE_ZOOM_PERCENT == 90
    assert image_has_expected_visual_occupancy(cyan, 100) is True
    assert image_has_expected_visual_occupancy(empty, 100) is False


def test_validation_cleanup_selects_only_metadata_and_allowlisted_ids() -> None:
    resource = {
        "id": "validation-parent",
        "extendedProperties": {
            "private": {
                "generated_by": "calendar-anim",
                "run_id": VALIDATION_RUN_ID,
                "calendar_profile": "account-b",
            }
        },
    }
    assert select_cleanup_ids([resource], {"validation-parent"}) == {"validation-parent"}

    with pytest.raises(CalendarAnimError, match="outside validation allowlist"):
        select_cleanup_ids([resource], {"another-parent"})

    polluted = {
        **resource,
        "extendedProperties": {
            "private": {
                "generated_by": "calendar-anim",
                "run_id": RUN_ID,
                "calendar_profile": "account-b",
            }
        },
    }
    with pytest.raises(CalendarAnimError, match="validation-only"):
        select_cleanup_ids([polluted], {"validation-parent"})

    ultra = {
        **resource,
        "id": "ultra-parent",
        "extendedProperties": {
            "private": {
                "generated_by": "calendar-anim",
                "run_id": VALIDATION_RUN_ID_168,
                "calendar_profile": "account-b",
            }
        },
    }
    assert select_cleanup_ids([ultra], {"ultra-parent"}, run_id=VALIDATION_RUN_ID_168) == {
        "ultra-parent"
    }
