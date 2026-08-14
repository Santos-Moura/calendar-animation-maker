import pytest

from calendar_anim.calendar.high_detail import (
    FINAL_CUTSCENE_RUN_ID,
    quota_wait_policy_for_run,
)

pytestmark = pytest.mark.unit


def test_final_run_uses_requested_long_quota_cooldowns() -> None:
    policy = quota_wait_policy_for_run(FINAL_CUTSCENE_RUN_ID)

    assert policy is not None
    assert policy.cooldown_seconds == (900.0, 1800.0, 3600.0, 7200.0, 14400.0)
    assert policy.cooldown_for_stage(99) == 14400.0
    assert policy.jitter_seconds == 180.0
    assert policy.max_auto_wait_seconds == 48 * 60 * 60


def test_automatic_quota_wait_does_not_change_other_run_defaults() -> None:
    assert quota_wait_policy_for_run("another-run") is None
