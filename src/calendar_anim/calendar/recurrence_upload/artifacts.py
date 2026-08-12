import hashlib
import json
import os
import statistics
from datetime import UTC, datetime
from pathlib import Path

from pydantic import ValidationError

from calendar_anim.calendar.models import CalendarWritePacingSnapshot
from calendar_anim.calendar.recurrence_compaction.models import (
    RecurrenceMigrationPlan,
    RecurrenceStudyReport,
)
from calendar_anim.calendar.recurrence_upload.models import (
    ParentUploadState,
    ParentUploadStatus,
    PayloadStatistics,
    RecurrenceDryRunReport,
    RecurrenceUploadPerformance,
    RecurrenceUploadState,
)
from calendar_anim.exceptions import CalendarAnimError

ARTIFACT_NAMES = (
    "account-b-animation-plan.json",
    "account-b-recurrence-plan.json",
    "account-b-recurrence-report.json",
    "hybrid-final-plan.json",
)
SAFE_PAYLOAD_BYTES = 32_000


def _write_atomic(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()
    return path


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class RecurrenceUploadStore:
    def __init__(
        self,
        plan_root: Path = Path("output/hybrid-plans"),
        state_root: Path = Path("output/hybrid-runs"),
    ) -> None:
        self.plan_root = plan_root
        self.state_root = state_root

    def plan_directory(self, run_id: str) -> Path:
        return self.plan_root / run_id

    def run_directory(self, run_id: str) -> Path:
        return self.state_root / run_id

    def state_path(self, run_id: str) -> Path:
        return self.run_directory(run_id) / "account-b-upload-state.json"

    def dry_run_path(self, run_id: str) -> Path:
        return self.run_directory(run_id) / "account-b-dry-run-report.json"

    def performance_json_path(self, run_id: str) -> Path:
        return self.run_directory(run_id) / "account-b-upload-performance.json"

    def performance_text_path(self, run_id: str) -> Path:
        return self.run_directory(run_id) / "account-b-upload-performance.txt"

    def artifact_hashes(self, run_id: str) -> dict[str, str]:
        directory = self.plan_directory(run_id)
        try:
            return {name: file_sha256(directory / name) for name in ARTIFACT_NAMES}
        except OSError as error:
            raise CalendarAnimError(
                f"Missing or unreadable hybrid artifact in {directory}"
            ) from error

    def load_plan(self, run_id: str) -> RecurrenceMigrationPlan:
        path = self.plan_directory(run_id) / "account-b-recurrence-plan.json"
        try:
            return RecurrenceMigrationPlan.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValidationError, ValueError) as error:
            raise CalendarAnimError(f"Invalid recurrence plan: {path}") from error

    def load_report(self, run_id: str) -> RecurrenceStudyReport:
        path = self.plan_directory(run_id) / "account-b-recurrence-report.json"
        try:
            return RecurrenceStudyReport.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValidationError, ValueError) as error:
            raise CalendarAnimError(f"Invalid recurrence report: {path}") from error

    def load_json(self, run_id: str, name: str) -> dict[str, object]:
        path = self.plan_directory(run_id) / name
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise CalendarAnimError(f"Invalid hybrid artifact: {path}") from error
        if not isinstance(value, dict):
            raise CalendarAnimError(f"Hybrid artifact is not an object: {path}")
        return value

    def initialize_state(
        self,
        run_id: str,
        plan: RecurrenceMigrationPlan,
        artifact_sha256: dict[str, str],
        minimum_interval_seconds: float = 1.0,
    ) -> RecurrenceUploadState:
        path = self.state_path(run_id)
        if path.exists():
            state = self.load_state(run_id)
            self.validate_state(state, plan, artifact_sha256)
            return state
        state = RecurrenceUploadState(
            run_id=run_id,
            plan_sha256=artifact_sha256["account-b-recurrence-plan.json"],
            artifact_sha256=artifact_sha256,
            parents=[ParentUploadState(parent_id=item.parent_id) for item in plan.parents],
            write_pacing=CalendarWritePacingSnapshot(
                minimum_interval_seconds=minimum_interval_seconds,
                current_interval_seconds=minimum_interval_seconds,
            ),
            updated_at=datetime.now(UTC),
        )
        self.save_state(state)
        return state

    def validate_state(
        self,
        state: RecurrenceUploadState,
        plan: RecurrenceMigrationPlan,
        hashes: dict[str, str],
    ) -> None:
        if state.artifact_sha256 != hashes:
            raise CalendarAnimError("Hybrid artifacts changed after upload state initialization")
        if [item.parent_id for item in state.parents] != [item.parent_id for item in plan.parents]:
            raise CalendarAnimError("Upload state parent sequence differs from recurrence plan")

    def save_state(self, state: RecurrenceUploadState) -> Path:
        state.updated_at = datetime.now(UTC)
        # There are 32k parent checkpoints in the final plan.  Omitting defaults and
        # nulls keeps the atomically-rewritten state small while Pydantic restores
        # those defaults on load.
        payload = state.model_dump_json(exclude_defaults=True, exclude_none=True)
        return _write_atomic(self.state_path(state.run_id), payload + "\n")

    def load_state(self, run_id: str) -> RecurrenceUploadState:
        path = self.state_path(run_id)
        try:
            return RecurrenceUploadState.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValidationError, ValueError) as error:
            raise CalendarAnimError(f"Invalid recurrence upload state: {path}") from error

    def save_dry_run(self, report: RecurrenceDryRunReport) -> Path:
        return _write_atomic(
            self.dry_run_path(report.run_id), report.model_dump_json(indent=2) + "\n"
        )

    def save_performance(self, report: RecurrenceUploadPerformance) -> tuple[Path, Path]:
        json_path = _write_atomic(
            self.performance_json_path(report.run_id), report.model_dump_json(indent=2) + "\n"
        )
        text_path = _write_atomic(
            self.performance_text_path(report.run_id), performance_text(report)
        )
        return json_path, text_path


def build_dry_run_report(
    run_id: str,
    plan: RecurrenceMigrationPlan,
    study: RecurrenceStudyReport,
    artifact_hashes: dict[str, str],
    source_sha256: str,
    expected_occurrence_keys: set[str] | None = None,
) -> RecurrenceDryRunReport:
    parent_ids = [item.parent_id for item in plan.parents]
    expanded = [key for item in plan.parents for key in item.occurrence_keys]
    expected = plan.remaining_occurrences
    payloads = sorted(item.estimated_insert_payload_bytes for item in plan.parents)
    if not payloads:
        raise CalendarAnimError("Recurrence plan contains no parents")
    p95 = payloads[max(0, (95 * len(payloads) + 99) // 100 - 1)]
    largest_rdate = max(max(0, item.occurrence_count - 1) for item in plan.parents)
    duplicates = len(expanded) - len(set(expanded))
    expanded_set = set(expanded)
    if expected_occurrence_keys is None:
        missing = max(0, expected - len(expanded_set))
        extra = max(0, len(expanded_set) - expected)
    else:
        missing = len(expected_occurrence_keys - expanded_set)
        extra = len(expanded_set - expected_occurrence_keys)
    if payloads[-1] > SAFE_PAYLOAD_BYTES:
        raise CalendarAnimError(
            f"Largest recurrence payload {payloads[-1]} exceeds safe limit {SAFE_PAYLOAD_BYTES}"
        )
    return RecurrenceDryRunReport(
        run_id=run_id,
        logical_occurrences=expected,
        parent_inserts=len(plan.parents),
        chunk_size=plan.parent_chunk_size,
        reduction_percent=study.migration_insert_reduction,
        unique_parent_ids=len(parent_ids) == len(set(parent_ids)),
        duplicate_parent_ids=len(parent_ids) - len(set(parent_ids)),
        duplicate_occurrences=duplicates,
        missing_occurrences=missing,
        extra_occurrences=extra,
        expansion_equality=plan.expansion_equals_missing
        and duplicates == missing == extra == 0
        and len(expanded) == expected,
        payload=PayloadStatistics(
            minimum_bytes=payloads[0],
            mean_bytes=statistics.fmean(payloads),
            p95_bytes=p95,
            maximum_bytes=payloads[-1],
            largest_rdate_count=largest_rdate,
            largest_occurrence_group=study.distribution.largest,
        ),
        source_sha256=source_sha256,
        artifact_sha256=artifact_hashes,
    )


def performance_from_state(
    state: RecurrenceUploadState, wall_clock_seconds: float
) -> RecurrenceUploadPerformance:
    total = len(state.parents)
    completed = state.completed_count
    failed = sum(item.status is ParentUploadStatus.FAILED for item in state.parents)
    pending = total - completed - failed
    rate = completed / state.active_upload_seconds if state.active_upload_seconds else 0.0
    active_eta = pending / rate if rate else None
    wait_ratio = state.quota_wait_total_seconds / max(state.active_upload_seconds, 1e-9)
    wall_eta = active_eta * (1 + wait_ratio) if active_eta is not None else None
    return RecurrenceUploadPerformance(
        run_id=state.run_id,
        total_parents_planned=total,
        parents_completed=completed,
        parents_pending=pending,
        parents_failed=failed,
        events_insert_calls=state.events_insert_calls,
        conflict_reconciliations=state.conflict_reconciliations,
        remote_reconciliations=state.remote_reconciliations,
        rate_limit_exceeded_count=state.rate_limit_exceeded_count,
        quota_exceeded_count=state.quota_exceeded_count,
        parent_retries=state.retry_count,
        quota_wait_entries=state.quota_wait_entries,
        quota_wait_attempts=state.quota_wait_attempts,
        quota_wait_total_seconds=state.quota_wait_total_seconds,
        active_upload_seconds=state.active_upload_seconds,
        wall_clock_seconds=wall_clock_seconds,
        parents_per_active_second=rate,
        active_upload_eta_seconds=active_eta,
        wall_clock_eta_seconds=wall_eta,
        current_write_interval_seconds=state.write_pacing.current_interval_seconds,
        last_rate_limit_timestamp=state.last_rate_limit_timestamp,
        next_quota_retry_timestamp=(
            state.quota_wait.next_retry_at if state.quota_wait is not None else None
        ),
        updated_at=datetime.now(UTC),
    )


def performance_text(report: RecurrenceUploadPerformance) -> str:
    return "\n".join(
        [
            "ACCOUNT-B RECURRENCE UPLOAD PERFORMANCE",
            "=======================================",
            f"Parents: {report.parents_completed}/{report.total_parents_planned}",
            f"Pending: {report.parents_pending}",
            f"Failed: {report.parents_failed}",
            f"events.insert calls: {report.events_insert_calls}",
            f"409 reconciliations: {report.conflict_reconciliations}",
            f"Remote reconciliations: {report.remote_reconciliations}",
            f"rateLimitExceeded: {report.rate_limit_exceeded_count}",
            f"quotaExceeded: {report.quota_exceeded_count}",
            f"Parent retries: {report.parent_retries}",
            f"Quota wait entries: {report.quota_wait_entries}",
            f"Quota wait seconds: {report.quota_wait_total_seconds:.2f}",
            f"Active upload seconds: {report.active_upload_seconds:.2f}",
            f"Wall clock seconds: {report.wall_clock_seconds:.2f}",
            f"Parents/second: {report.parents_per_active_second:.6f}",
            f"Active ETA seconds: {report.active_upload_eta_seconds}",
            f"Wall ETA seconds: {report.wall_clock_eta_seconds}",
            f"Current interval seconds: {report.current_write_interval_seconds:.2f}",
            "",
        ]
    )
