from datetime import date

import pytest

from calendar_anim.calendar.dry_run import DryRunCalendarGateway
from calendar_anim.calendar.mapper import plan_events
from tests.factories import make_manifest

pytestmark = pytest.mark.unit


def test_plan_uses_consecutive_weeks_and_private_metadata() -> None:
    manifest = make_manifest()
    second = manifest.frames[0].model_copy(update={"index": 1})
    manifest.frames.append(second)
    manifest.render.frame_count = 2
    plan = plan_events(manifest, date(2026, 8, 10), "America/Sao_Paulo")
    assert (plan.events[1].start.date() - plan.events[0].start.date()).days == 7
    assert plan.events[0].private_metadata["animation_id"] == "test-animation"


def test_dry_run_gateway_only_deletes_matching_animation() -> None:
    plan = plan_events(make_manifest(), date(2026, 8, 10), "UTC")
    gateway = DryRunCalendarGateway()
    gateway.create_events("dry", plan.events)
    assert len(gateway.events) == 1
    assert gateway.delete_animation_events("dry", "other") == 0
    assert gateway.delete_animation_events("dry", "test-animation") == 1
