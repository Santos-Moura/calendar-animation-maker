from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field


class ExpectedOccurrence(BaseModel):
    occurrence_key: str
    parent_id: str
    chunk_index: int = Field(ge=0)
    frame_index: int = Field(ge=0)
    start: datetime
    end: datetime
    timezone: str
    summary: str
    summary_codepoints: list[str]
    color_id: str | None
    role: str


class RemoteOccurrence(BaseModel):
    event_id: str
    parent_id: str
    recurring_event_id: str | None
    original_start_time: datetime | None
    start: datetime
    end: datetime
    start_timezone: str | None
    end_timezone: str | None
    summary: str
    summary_codepoints: list[str]
    color_id: str | None
    private_metadata: dict[str, str]


class Divergence(BaseModel):
    category: str
    parent_id: str | None = None
    expected: ExpectedOccurrence | None = None
    remote: RemoteOccurrence | None = None
    differing_fields: list[str] = Field(default_factory=list)


class ParentAudit(BaseModel):
    parent_id: str
    chunk_index: int
    local_dtstart: datetime
    local_dtend: datetime
    local_recurrence: list[str]
    local_occurrence_count: int
    local_expected_dates: list[str]
    base_in_rdate: bool
    remote_found: bool
    remote_dtstart: dict[str, object] | None = None
    remote_dtend: dict[str, object] | None = None
    remote_recurrence: list[str] = Field(default_factory=list)
    remote_summary: str | None = None
    remote_summary_codepoints: list[str] = Field(default_factory=list)
    remote_color_id: str | None = None
    remote_transparency: str | None = None
    remote_visibility: str | None = None
    remote_event_type: str | None = None
    remote_private_metadata: dict[str, str] = Field(default_factory=dict)
    payload_matches: bool
    remote_occurrence_dates: list[str] = Field(default_factory=list)


class FrameRemoteAudit(BaseModel):
    human_frame: int
    frame_index: int
    week_start: date
    expected_occurrences: int
    google_expanded_occurrences: int
    exact_matches: int
    missing: int
    extra: int
    duplicates: int
    wrong_date: int
    wrong_time: int
    wrong_summary: int
    wrong_color: int
    wrong_parent_mapping: int
    expected_set: list[ExpectedOccurrence]
    remote_expanded_set: list[RemoteOccurrence]
    first_divergences: list[Divergence]
    parent_audits: list[ParentAudit]


RootCause = Literal["A", "B", "C", "D", "E", "F", "G"]


class PlanInvariantAudit(BaseModel):
    parents_checked: int
    chunk_size: int
    chunk_size_violations: int
    base_in_rdate_count: int
    recurrence_cardinality_mismatches: int
    unsorted_rdate_parents: int
    signature_fields_included: list[str]
    signature_fields_excluded: list[str]


class RemoteRecurrenceAuditReport(BaseModel):
    schema_version: str = "1.0"
    run_id: str
    profile: str
    calendar_name: str
    calendar_id: str
    timezone: str
    generated_at: datetime
    frames_audited: list[int]
    frames: list[FrameRemoteAudit]
    total_expected_occurrences: int
    total_google_expanded_occurrences: int
    total_exact_matches: int
    total_missing: int
    total_extra: int
    total_duplicates: int
    plan_invariants: PlanInvariantAudit
    root_cause_category: RootCause
    root_cause: str
    recurrence_mechanism_broken: Literal["YES", "NO", "PARTIAL", "UNKNOWN"]
    planner_grouping_wrong: Literal["YES", "NO", "UNKNOWN"]
    existing_bulk_salvageable_without_recreation: Literal["YES", "NO", "UNKNOWN"]
    google_calendar_reads: bool = True
    google_calendar_writes: bool = False
