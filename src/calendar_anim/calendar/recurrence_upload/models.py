from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field, model_validator

from calendar_anim.calendar.models import CalendarWritePacingSnapshot
from calendar_anim.calendar.multi_frame.models import QuotaWaitState


class ParentUploadStatus(StrEnum):
    PENDING = "pending"
    UPLOADING = "uploading"
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"


class ParentUploadState(BaseModel):
    parent_id: str
    status: ParentUploadStatus = ParentUploadStatus.PENDING
    insert_calls: int = Field(default=0, ge=0)
    retry_count: int = Field(default=0, ge=0)
    reconciled: bool = False
    last_error: str | None = None
    completed_at: datetime | None = None


class RecurrenceUploadState(BaseModel):
    schema_version: str = "1.0"
    run_id: str
    calendar_profile: str = "account-b"
    calendar_id: str | None = None
    plan_sha256: str
    artifact_sha256: dict[str, str]
    parents: list[ParentUploadState]
    write_pacing: CalendarWritePacingSnapshot
    quota_wait: QuotaWaitState | None = None
    rate_limit_exceeded_count: int = Field(default=0, ge=0)
    last_rate_limit_timestamp: datetime | None = None
    quota_exceeded_count: int = Field(default=0, ge=0)
    events_insert_calls: int = Field(default=0, ge=0)
    conflict_reconciliations: int = Field(default=0, ge=0)
    remote_reconciliations: int = Field(default=0, ge=0)
    retry_count: int = Field(default=0, ge=0)
    quota_wait_entries: int = Field(default=0, ge=0)
    quota_wait_attempts: int = Field(default=0, ge=0)
    quota_wait_total_seconds: float = Field(default=0, ge=0)
    quota_recoveries: int = Field(default=0, ge=0)
    active_upload_seconds: float = Field(default=0, ge=0)
    started_at: datetime | None = None
    updated_at: datetime

    @model_validator(mode="after")
    def unique_parents(self) -> "RecurrenceUploadState":
        ids = [item.parent_id for item in self.parents]
        if len(ids) != len(set(ids)):
            raise ValueError("recurrence upload state contains duplicate parent IDs")
        return self

    @property
    def completed_count(self) -> int:
        return sum(item.status is ParentUploadStatus.COMPLETED for item in self.parents)

    def parent(self, parent_id: str) -> ParentUploadState:
        return next(item for item in self.parents if item.parent_id == parent_id)


class PayloadStatistics(BaseModel):
    minimum_bytes: int = Field(ge=0)
    mean_bytes: float = Field(ge=0)
    p95_bytes: int = Field(ge=0)
    maximum_bytes: int = Field(ge=0)
    largest_rdate_count: int = Field(ge=0)
    largest_occurrence_group: int = Field(ge=0)


class RecurrenceDryRunReport(BaseModel):
    schema_version: str = "1.0"
    run_id: str
    logical_occurrences: int
    parent_inserts: int
    chunk_size: int
    reduction_percent: float
    unique_parent_ids: bool
    duplicate_parent_ids: int
    duplicate_occurrences: int
    missing_occurrences: int
    extra_occurrences: int
    expansion_equality: bool
    payload: PayloadStatistics
    source_sha256: str
    artifact_sha256: dict[str, str]
    google_calendar_writes: bool = False


class RecurrenceUploadPerformance(BaseModel):
    schema_version: str = "1.0"
    run_id: str
    total_parents_planned: int
    parents_completed: int
    parents_pending: int
    parents_failed: int
    events_insert_calls: int
    conflict_reconciliations: int
    remote_reconciliations: int
    rate_limit_exceeded_count: int
    quota_exceeded_count: int
    parent_retries: int
    quota_wait_entries: int
    quota_wait_attempts: int
    quota_wait_total_seconds: float
    active_upload_seconds: float
    wall_clock_seconds: float
    parents_per_active_second: float
    active_upload_eta_seconds: float | None
    wall_clock_eta_seconds: float | None
    current_write_interval_seconds: float
    last_rate_limit_timestamp: datetime | None = None
    next_quota_retry_timestamp: datetime | None = None
    updated_at: datetime
