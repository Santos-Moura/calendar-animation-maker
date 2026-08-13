from datetime import date, timedelta

from pydantic import BaseModel, Field, model_validator


class FrameOccurrenceStatistics(BaseModel):
    minimum: int = Field(ge=0)
    mean: float = Field(ge=0)
    p95: int = Field(ge=0)
    maximum: int = Field(ge=0)


class PayloadSizing(BaseModel):
    minimum_bytes: int = Field(ge=0)
    mean_bytes: float = Field(ge=0)
    p95_bytes: int = Field(ge=0)
    maximum_bytes: int = Field(ge=0)
    safe_limit_bytes: int = 32_000
    within_safe_limit: bool


class Cayde216SizingReport(BaseModel):
    schema_version: str = "1.0"
    run_id: str
    source_file: str
    source_sha256: str
    clip_start_seconds: float
    clip_end_seconds: float
    duration_seconds: float
    fps: float
    frame_count: int
    frame_indices: list[int]
    calendar_profile: str
    calendar_name: str
    timezone: str
    palette_preset: str
    background_color_id: str
    foreground_color_ids: list[str]
    first_week: date
    last_week: date
    week_count: int
    all_week_deltas_seven_days: bool
    old_first_week: date
    old_last_week: date
    old_week_overlap: int
    logical_occurrences: int
    frame_occurrences: FrameOccurrenceStatistics
    unique_recurrence_signatures: int
    recurring_parents: int
    reduction_percent: float
    singleton_parents: int
    largest_group: int
    largest_chunk: int
    largest_rdate_count: int
    expansion_missing: int
    expansion_extra: int
    expansion_duplicates: int
    expansion_exact: bool
    parent_ids_unique: bool
    parent_id_collisions_with_existing_b: int
    existing_b_parent_count: int
    payload: PayloadSizing
    eta_seconds: dict[str, float]
    old_frames: int = 108
    old_fps: float = 3.0
    old_duration_seconds: float = 36.0
    old_logical_occurrences: int = 277_830
    old_account_b_parents: int = 46_468
    logical_occurrence_ratio: float
    parent_count_ratio: float
    upload_eta_ratio: float
    capture_profile: str = "account-b"
    capture_zoom_percent: int = 90
    capture_mode: str = "header_preserved_fill"
    capture_resolution: str = "1512x864"
    left_time_gutter: bool = True
    header: bool = True
    visible_interval: str = "06:00-00:00"
    pre_06_blank_gap: bool = False
    readiness_protection: str
    future_preview_human_frames: list[int]
    composer_first_frame: str = "frame_000.png"
    composer_last_frame: str = "frame_215.png"
    composer_frame_count: int = 216
    composer_fps: float = 6.0
    old_version_touched: bool = False
    old_final_outputs_touched: bool = False
    old_protected_sha256_before: dict[str, str]
    old_protected_sha256_after: dict[str, str]
    google_calendar_reads: bool = False
    google_calendar_writes: bool = False

    @model_validator(mode="after")
    def safety_gates(self) -> "Cayde216SizingReport":
        failures = []
        if self.frame_count != 216 or self.frame_indices != list(range(216)):
            failures.append("frame sequence")
        if self.fps != 6 or self.duration_seconds != 36 or self.frame_count / self.fps != 36:
            failures.append("timing")
        if self.week_count != 216 or not self.all_week_deltas_seven_days:
            failures.append("week sequence")
        if (
            self.palette_preset != "cayde-cyan-magenta"
            or self.background_color_id != "7"
            or self.foreground_color_ids != ["3", "5", "9", "11"]
        ):
            failures.append("final palette")
        if self.old_week_overlap:
            failures.append("old week overlap")
        if not self.expansion_exact:
            failures.append("recurrence expansion")
        if not self.parent_ids_unique or self.parent_id_collisions_with_existing_b:
            failures.append("parent IDs")
        if not self.payload.within_safe_limit:
            failures.append("payload size")
        if self.old_version_touched or self.old_final_outputs_touched:
            failures.append("old version protection")
        if failures:
            raise ValueError("216-frame safety gates failed: " + ", ".join(failures))
        return self


class Cayde216RemotePreflight(BaseModel):
    schema_version: str = "1.0"
    run_id: str
    profile: str
    authenticated_account: str
    expected_calendar_id: str
    remote_calendar_id: str
    calendar_name: str
    access_role: str
    timezone: str
    range_start: date
    range_end_exclusive: date
    unexpected_event_count: int
    unexpected_event_ids: list[str]
    new_range_clean: bool
    old_artifacts_unchanged: bool
    old_resources_touched: bool = False
    google_calendar_reads: bool = True
    google_calendar_writes: bool = False
    result: str


class Cayde216WindowCandidate(BaseModel):
    rank: int = Field(ge=1)
    first_week: date
    last_week: date
    end_exclusive: date
    week_count: int = 216
    conflicting_events: int = Field(default=0, ge=0)
    overlaps_old_run: bool = False

    @model_validator(mode="after")
    def clean_window(self) -> "Cayde216WindowCandidate":
        if self.last_week != self.first_week + timedelta(weeks=215):
            raise ValueError("window last week must be exactly frame 216")
        if self.end_exclusive != self.first_week + timedelta(weeks=216):
            raise ValueError("window end must be half-open after 216 weeks")
        if self.conflicting_events or self.overlaps_old_run:
            raise ValueError("window candidate must be clean and disjoint from old run")
        return self


class Cayde216WindowSearchReport(BaseModel):
    schema_version: str = "1.0"
    run_id: str
    profile: str
    authenticated_account: str
    calendar_id: str
    calendar_name: str
    timezone: str
    query_start: date
    query_end_exclusive: date
    expanded_events_seen: int = Field(ge=0)
    candidates: list[Cayde216WindowCandidate]
    old_artifacts_unchanged: bool
    old_resources_touched: bool = False
    google_calendar_reads: bool = True
    google_calendar_writes: bool = False
    result: str

    @model_validator(mode="after")
    def enough_clean_windows(self) -> "Cayde216WindowSearchReport":
        if self.result == "PASS" and len(self.candidates) < 2:
            raise ValueError("PASS requires two clean 216-week candidates")
        return self
