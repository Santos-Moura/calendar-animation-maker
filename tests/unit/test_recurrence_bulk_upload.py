from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from calendar_anim.calendar.models import CalendarWritePacingSnapshot
from calendar_anim.calendar.multi_frame.quota_wait import QuotaWaitPolicy
from calendar_anim.calendar.multi_frame.retry import UploadRetryPolicy
from calendar_anim.calendar.recurrence_compaction.models import (
    RecurrenceMigrationPlan,
    RecurrenceSignature,
    RecurringParentPlan,
)
from calendar_anim.calendar.recurrence_upload.artifacts import RecurrenceUploadStore
from calendar_anim.calendar.recurrence_upload.gateway import (
    GoogleRecurrenceUploadGateway,
    ParentInsertError,
)
from calendar_anim.calendar.recurrence_upload.models import (
    ParentUploadState,
    ParentUploadStatus,
    RecurrenceUploadState,
)
from calendar_anim.calendar.recurrence_upload.service import RecurrenceUploadService
from calendar_anim.exceptions import CalendarAnimError


def parent(index: int) -> RecurringParentPlan:
    start = datetime(2030, 1, 6 + index, 6, tzinfo=UTC)
    signature = RecurrenceSignature(
        timezone="UTC",
        day_of_week=start.weekday(),
        local_start_time="06:00:00",
        duration_seconds=900,
        summary="\u200b\u200b",
        color_id="1",
    )
    return RecurringParentPlan(
        parent_id=f"cr{index:064x}",
        recurrence_group_id=f"group-{index}",
        signature_hash=f"hash-{index}",
        chunk_index=0,
        signature=signature,
        start=start,
        end=start + timedelta(minutes=15),
        recurrence=["RDATE;TZID=UTC:20300201T060000"],
        occurrence_keys=[f"f0023:event-{index}-0", f"f0024:event-{index}-1"],
        covered_frame_indices=[23, 24],
        private_metadata={
            "generated_by": "calendar-anim",
            "run_id": "hybrid-test",
            "calendar_profile": "account-b",
            "recurrence_group_id": f"group-{index}",
            "signature_hash": f"hash-{index}",
            "chunk_index": "0",
        },
        estimated_insert_payload_bytes=700,
        calendar_profile="account-b",
    )


def plan(count: int = 3) -> RecurrenceMigrationPlan:
    parents = [parent(index) for index in range(count)]
    return RecurrenceMigrationPlan(
        source_run_id="source",
        generated_at=datetime.now(UTC),
        timezone="UTC",
        parent_chunk_size=100,
        existing_single_event_ids=[],
        completed_frame_indices=[],
        partial_frame_indices=[],
        remaining_occurrences=count * 2,
        parents=parents,
        expanded_occurrence_count=count * 2,
        duplicate_occurrences=0,
        expansion_equals_missing=True,
    )


def state(plan_value: RecurrenceMigrationPlan) -> RecurrenceUploadState:
    return RecurrenceUploadState(
        run_id="hybrid-test",
        plan_sha256="a" * 64,
        artifact_sha256={"plan": "a" * 64},
        parents=[ParentUploadState(parent_id=item.parent_id) for item in plan_value.parents],
        write_pacing=CalendarWritePacingSnapshot(
            minimum_interval_seconds=1,
            current_interval_seconds=1,
        ),
        updated_at=datetime.now(UTC),
    )


class FakeClock:
    def __init__(self) -> None:
        self.seconds = 0.0
        self.base = datetime(2030, 1, 1, tzinfo=UTC)

    def monotonic(self) -> float:
        return self.seconds

    def now(self) -> datetime:
        return self.base + timedelta(seconds=self.seconds)

    def sleep(self, seconds: float) -> None:
        self.seconds += seconds


class FakeGateway:
    def __init__(self) -> None:
        self.helper = GoogleRecurrenceUploadGateway(None)
        self.remote: dict[str, dict[str, object]] = {}
        self.actions: list[object] = []
        self.inserted: list[str] = []
        self.pacing = CalendarWritePacingSnapshot(
            minimum_interval_seconds=1, current_interval_seconds=1
        )

    def restore_write_pacing(self, snapshot: CalendarWritePacingSnapshot) -> None:
        self.pacing = snapshot

    def configure_write_pacing(
        self,
        minimum_interval_seconds: float,
        *,
        current_interval_seconds: float | None = None,
        **_kwargs: object,
    ) -> None:
        self.pacing = CalendarWritePacingSnapshot(
            minimum_interval_seconds=minimum_interval_seconds,
            current_interval_seconds=current_interval_seconds or minimum_interval_seconds,
        )

    def write_pacing_snapshot(self) -> CalendarWritePacingSnapshot:
        return self.pacing

    def parent_body(self, value: RecurringParentPlan) -> dict[str, object]:
        return self.helper.parent_body(value)

    def parent_matches(self, remote: dict[str, object], value: RecurringParentPlan) -> bool:
        return remote == self.parent_body(value)

    def get_parent(self, _calendar_id: str, parent_id: str):  # type: ignore[no-untyped-def]
        return self.remote.get(parent_id)

    def insert_parent(self, _calendar_id: str, value: RecurringParentPlan) -> str:
        action = self.actions.pop(0) if self.actions else "success"
        if isinstance(action, BaseException):
            raise action
        if action == "lost-response":
            self.remote[value.parent_id] = self.parent_body(value)
            raise ParentInsertError("lost", retryable=True)
        self.inserted.append(value.parent_id)
        self.remote[value.parent_id] = self.parent_body(value)
        return value.parent_id


def service(tmp_path: Path, gateway: FakeGateway, clock: FakeClock) -> RecurrenceUploadService:
    return RecurrenceUploadService(
        gateway,  # type: ignore[arg-type]
        RecurrenceUploadStore(tmp_path / "plans", tmp_path / "state"),
        retry_policy=UploadRetryPolicy(
            max_event_attempts=3,
            base_delay_seconds=1,
            max_delay_seconds=2,
            jitter_seconds=0,
            rate_limit_base_delay_seconds=1,
            rate_limit_max_delay_seconds=2,
        ),
        quota_policy=QuotaWaitPolicy(
            cooldown_seconds=(1, 2),
            jitter_seconds=0,
            max_auto_wait_seconds=20,
            conservative_recovery_interval_seconds=1.5,
        ),
        now=clock.now,
        clock=clock.monotonic,
        sleeper=clock.sleep,
        jitter=lambda _maximum: 0,
        checkpoint_interval=1,
    )


def test_completed_parents_are_skipped_on_resume(tmp_path: Path) -> None:
    plan_value = plan()
    state_value = state(plan_value)
    state_value.parents[0].status = ParentUploadStatus.COMPLETED
    gateway, clock = FakeGateway(), FakeClock()

    uploaded = service(tmp_path, gateway, clock).upload(plan_value, state_value, "calendar-b")

    assert uploaded.completed_count == 3
    assert gateway.inserted == [item.parent_id for item in plan_value.parents[1:]]


def test_partial_parent_is_reconciled_without_insert(tmp_path: Path) -> None:
    plan_value = plan(1)
    state_value = state(plan_value)
    state_value.parents[0].status = ParentUploadStatus.PARTIAL
    gateway, clock = FakeGateway(), FakeClock()
    gateway.remote[plan_value.parents[0].parent_id] = gateway.parent_body(plan_value.parents[0])

    uploaded = service(tmp_path, gateway, clock).upload(plan_value, state_value, "calendar-b")

    assert uploaded.parents[0].reconciled
    assert uploaded.remote_reconciliations == 1
    assert gateway.inserted == []


def test_409_expected_parent_is_reconciled_success(tmp_path: Path) -> None:
    plan_value = plan(1)
    gateway, clock = FakeGateway(), FakeClock()
    gateway.remote[plan_value.parents[0].parent_id] = gateway.parent_body(plan_value.parents[0])
    gateway.actions = [ParentInsertError("conflict", status_code=409)]

    uploaded = service(tmp_path, gateway, clock).upload(plan_value, state(plan_value), "calendar-b")

    assert uploaded.conflict_reconciliations == 1
    assert uploaded.completed_count == 1


def test_lost_response_reconciles_without_duplicate(tmp_path: Path) -> None:
    plan_value = plan(1)
    gateway, clock = FakeGateway(), FakeClock()
    gateway.actions = ["lost-response"]

    uploaded = service(tmp_path, gateway, clock).upload(plan_value, state(plan_value), "calendar-b")

    assert uploaded.remote_reconciliations == 1
    assert uploaded.completed_count == 1
    assert gateway.inserted == []


def test_rate_limit_uses_retry_and_completes(tmp_path: Path) -> None:
    plan_value = plan(1)
    gateway, clock = FakeGateway(), FakeClock()
    gateway.actions = [
        ParentInsertError("rate", rate_limited=True, retryable=True),
        "success",
    ]

    uploaded = service(tmp_path, gateway, clock).upload(plan_value, state(plan_value), "calendar-b")

    assert uploaded.rate_limit_exceeded_count == 1
    assert uploaded.retry_count == 1
    assert uploaded.completed_count == 1


def test_quota_wait_uses_real_parent_probe_and_recovers(tmp_path: Path) -> None:
    plan_value = plan(1)
    gateway, clock = FakeGateway(), FakeClock()
    gateway.actions = [
        ParentInsertError("quota", quota_exceeded=True),
        ParentInsertError("quota", quota_exceeded=True),
        "success",
    ]

    uploaded = service(tmp_path, gateway, clock).upload(plan_value, state(plan_value), "calendar-b")

    assert uploaded.completed_count == 1
    assert uploaded.quota_exceeded_count == 2
    assert uploaded.quota_wait_attempts == 2
    assert uploaded.quota_recoveries == 1
    assert uploaded.quota_wait is None


def test_ctrl_c_checkpoints_uploading_parent_for_resume(tmp_path: Path) -> None:
    plan_value = plan(1)
    gateway, clock = FakeGateway(), FakeClock()
    gateway.actions = [KeyboardInterrupt()]
    store = RecurrenceUploadStore(tmp_path / "plans", tmp_path / "state")
    state_value = state(plan_value)
    store.save_state(state_value)
    runner = RecurrenceUploadService(
        gateway,  # type: ignore[arg-type]
        store,
        now=clock.now,
        clock=clock.monotonic,
        sleeper=clock.sleep,
        checkpoint_interval=1,
    )

    with pytest.raises(KeyboardInterrupt):
        runner.upload(plan_value, state_value, "calendar-b")

    saved = store.load_state(state_value.run_id)
    assert saved.parents[0].status is ParentUploadStatus.UPLOADING


def test_account_a_state_is_rejected_before_write(tmp_path: Path) -> None:
    plan_value = plan(1)
    state_value = state(plan_value)
    state_value.calendar_profile = "account-a"
    gateway, clock = FakeGateway(), FakeClock()

    with pytest.raises(CalendarAnimError, match="restricted to account-b"):
        service(tmp_path, gateway, clock).upload(plan_value, state_value, "calendar-a")

    assert gateway.inserted == []


def test_parent_body_preserves_visual_signature_and_remote_api_defaults() -> None:
    value = parent(0)
    gateway = GoogleRecurrenceUploadGateway(None)
    body = gateway.parent_body(value)

    assert body["summary"] == "\u200b\u200b"
    assert body["colorId"] == "1"
    assert body["start"] == {
        "dateTime": value.start.isoformat(),
        "timeZone": "UTC",
    }
    assert body["end"] == {"dateTime": value.end.isoformat(), "timeZone": "UTC"}
    assert body["recurrence"] == value.recurrence
    assert body["extendedProperties"] == {"private": value.private_metadata}

    remote = dict(body)
    remote.pop("transparency")
    remote.pop("visibility")
    remote.pop("eventType")
    assert gateway.parent_matches(remote, value)

    remote["colorId"] = "2"
    assert not gateway.parent_matches(remote, value)


def test_quota_probe_rate_limit_returns_to_adaptive_retry(tmp_path: Path) -> None:
    plan_value = plan(1)
    gateway, clock = FakeGateway(), FakeClock()
    gateway.actions = [
        ParentInsertError("quota", quota_exceeded=True),
        ParentInsertError("rate", rate_limited=True, retryable=True),
        "success",
    ]

    uploaded = service(tmp_path, gateway, clock).upload(plan_value, state(plan_value), "calendar-b")

    assert uploaded.completed_count == 1
    assert uploaded.quota_exceeded_count == 1
    assert uploaded.rate_limit_exceeded_count == 1
    assert uploaded.retry_count == 1
    assert uploaded.last_rate_limit_timestamp is not None


def test_quota_probe_lost_response_reconciles_without_duplicate(tmp_path: Path) -> None:
    plan_value = plan(1)
    gateway, clock = FakeGateway(), FakeClock()
    gateway.actions = [ParentInsertError("quota", quota_exceeded=True), "lost-response"]

    uploaded = service(tmp_path, gateway, clock).upload(plan_value, state(plan_value), "calendar-b")

    assert uploaded.completed_count == 1
    assert uploaded.remote_reconciliations == 1
    assert uploaded.quota_recoveries == 1
    assert gateway.inserted == []


def test_state_is_compact_atomic_and_rejects_changed_artifacts(tmp_path: Path) -> None:
    plan_value = plan()
    state_value = state(plan_value)
    store = RecurrenceUploadStore(tmp_path / "plans", tmp_path / "state")

    path = store.save_state(state_value)

    assert '"status"' not in path.read_text(encoding="utf-8")
    assert store.load_state(state_value.run_id).parents[0].status is ParentUploadStatus.PENDING
    with pytest.raises(CalendarAnimError, match="artifacts changed"):
        store.validate_state(state_value, plan_value, {"plan": "b" * 64})
