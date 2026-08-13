from datetime import date

import pytest

from calendar_anim.calendar.hybrid_capture.artifacts import (
    AccountBSingleCaptureStore,
    HybridCaptureStore,
)
from calendar_anim.calendar.hybrid_capture.models import HybridOutputMode
from calendar_anim.calendar.hybrid_capture.planner import (
    build_account_b_single_profile_capture_plan,
)
from tests.unit.test_account_b_prefix import _source_plan

pytestmark = pytest.mark.unit


def test_single_profile_capture_maps_all_108_frames_to_account_b_zoom_90() -> None:
    plan = build_account_b_single_profile_capture_plan(_source_plan())

    assert plan.capture_strategy == "single-profile-account-b"
    assert [frame.human_frame for frame in plan.frames] == list(range(1, 109))
    assert all(frame.calendar_profile == "account-b" for frame in plan.frames)
    assert all(frame.capture_zoom_percent == 90 for frame in plan.frames)
    assert plan.frames[0].week_start == date(2027, 10, 10)
    assert plan.frames[22].week_start == date(2028, 3, 12)
    assert plan.frames[23].week_start == date(2028, 3, 19)
    assert plan.frames[-1].week_start == date(2029, 10, 28)
    assert all(
        (right.week_start - left.week_start).days == 7
        for left, right in zip(plan.frames, plan.frames[1:], strict=False)
    )


def test_single_profile_artifacts_are_isolated_from_trusted_hybrid_paths(tmp_path) -> None:
    hybrid = HybridCaptureStore(tmp_path)
    single = AccountBSingleCaptureStore(tmp_path)
    run_id = "final-run"
    mode = HybridOutputMode.HEADER_PRESERVED_FILL
    resolution = (1512, 864)

    assert single.plan_path(run_id) != hybrid.plan_path(run_id)
    assert single.state_path(run_id, mode, resolution) != hybrid.state_path(
        run_id, mode, resolution
    )
    assert single.final_frame_path(run_id, 0, mode, resolution) != hybrid.final_frame_path(
        run_id, 0, mode, resolution
    )
