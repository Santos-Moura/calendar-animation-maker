from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

OccurrenceRole = Literal["background", "foreground", "unknown"]


class RecurrenceSignature(BaseModel):
    model_config = ConfigDict(frozen=True)

    timezone: str
    day_of_week: int = Field(ge=0, le=6)
    local_start_time: str
    duration_seconds: int = Field(gt=0)
    summary: str
    color_id: str | None = None
    transparency: str = "opaque"
    visibility: str = "default"
    event_type: str = "default"


class PlannedOccurrence(BaseModel):
    occurrence_key: str
    frame_index: int = Field(ge=0)
    start: datetime
    end: datetime
    role: OccurrenceRole
    original_event_id: str


class RecurringParentPlan(BaseModel):
    parent_id: str
    recurrence_group_id: str
    signature_hash: str
    chunk_index: int = Field(ge=0)
    signature: RecurrenceSignature
    start: datetime
    end: datetime
    recurrence: list[str] = Field(default_factory=list)
    occurrence_keys: list[str]
    covered_frame_indices: list[int]
    private_metadata: dict[str, str]
    estimated_insert_payload_bytes: int = Field(ge=0)

    @property
    def occurrence_count(self) -> int:
        return len(self.occurrence_keys)


class GroupDistribution(BaseModel):
    singleton: int = Field(ge=0)
    two_to_five: int = Field(ge=0)
    six_to_ten: int = Field(ge=0)
    eleven_to_twenty_five: int = Field(ge=0)
    twenty_six_to_fifty: int = Field(ge=0)
    fifty_one_to_one_hundred: int = Field(ge=0)
    over_one_hundred: int = Field(ge=0)
    mean: float = Field(ge=0)
    median: float = Field(ge=0)
    p95: float = Field(ge=0)
    largest: int = Field(ge=0)


class ScopeCompaction(BaseModel):
    occurrences: int = Field(ge=0)
    unique_signatures: int = Field(ge=0)
    parents_unlimited: int = Field(ge=0)
    parents_by_chunk: dict[int, int]
    reduction_by_chunk: dict[str, float]


class RecurrenceStudyReport(BaseModel):
    schema_version: str = "1.0"
    run_id: str
    generated_at: datetime
    timezone: str
    current_independent_inserts: int = Field(ge=0)
    rendered_instances: int = Field(ge=0)
    unique_exact_signatures: int = Field(ge=0)
    distribution: GroupDistribution
    full_scope: ScopeCompaction
    background: ScopeCompaction
    foreground: ScopeCompaction
    chunk_sizes: list[int]
    signature_fields_included: list[str]
    signature_fields_excluded: list[str]
    recurring_event_instance_limit: int = 730
    rdate_count_limit: str = "UNKNOWN"
    request_body_size_limit: str = "UNKNOWN"
    recurrence_array_size_limit: str = "UNKNOWN"
    general_usage_limit_instance_accounting: str = "UNKNOWN"
    batch_solves_quota: bool = False
    expanded_full_set_equals_original: bool
    full_duplicate_occurrences: int = Field(ge=0)
    completed_frames_preserved: int = Field(ge=0)
    partial_single_events_preserved: int = Field(ge=0)
    all_existing_single_events_preserved: int = Field(ge=0)
    remaining_occurrences: int = Field(ge=0)
    migration_parent_chunk_size: int = Field(gt=0)
    migration_parents_required: int = Field(ge=0)
    migration_insert_reduction: float = Field(ge=0, le=100)
    migration_duplicate_occurrences: int = Field(ge=0)
    migration_expansion_equals_missing: bool
    largest_migration_payload_bytes: int = Field(ge=0)
    mean_migration_payload_bytes: float = Field(ge=0)
    real_calendar_validation_required: bool = True
    google_calendar_writes: bool = False


class RecurrenceMigrationPlan(BaseModel):
    schema_version: str = "1.0"
    source_run_id: str
    generated_at: datetime
    timezone: str
    parent_chunk_size: int = Field(gt=0)
    existing_single_event_ids: list[str]
    completed_frame_indices: list[int]
    partial_frame_indices: list[int]
    remaining_occurrences: int = Field(ge=0)
    parents: list[RecurringParentPlan]
    expanded_occurrence_count: int = Field(ge=0)
    duplicate_occurrences: int = Field(ge=0)
    expansion_equals_missing: bool
