from datetime import date, datetime
from enum import StrEnum

from pydantic import BaseModel, Field, model_validator

from calendar_anim.calendar.frame_mapping.models import EventCompressionMode, FrameMappingMode
from calendar_anim.calendar.models import CalendarWritePacingSnapshot
from calendar_anim.calendar.subcolumn_ordering import SubcolumnOrderStrategy


class FrameUploadStatus(StrEnum):
    PENDING = "pending"
    UPLOADING = "uploading"
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"


class UploadPauseReason(StrEnum):
    CALENDAR_USAGE_QUOTA_EXCEEDED = "calendar_usage_quota_exceeded"


class UploadPauseMetadata(BaseModel):
    reason: UploadPauseReason
    http_status: int = Field(ge=100, le=599)
    google_reason: str
    frame_index: int = Field(ge=0)
    timestamp: datetime
    created_before_pause: int = Field(ge=0)
    planned_events: int = Field(ge=0)

    @property
    def remaining_events(self) -> int:
        return max(0, self.planned_events - self.created_before_pause)


class QuotaWaitState(BaseModel):
    frame_index: int = Field(ge=0)
    entered_at: datetime
    last_accounted_at: datetime
    next_retry_at: datetime
    max_wait_until: datetime
    stage_index: int = Field(ge=0)
    attempts: int = Field(default=0, ge=0)
    last_cooldown_seconds: float = Field(gt=0)
    exhausted: bool = False


class FrameUploadPlan(BaseModel):
    frame_index: int = Field(ge=0)
    source_timestamp_seconds: float | None = Field(default=None, ge=0)
    week_start: date
    frame_run_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
    planned_events: int = Field(ge=0)
    artifact_directory: str = Field(pattern=r"^frames/frame-[0-9]{4,}$")


class MultiFramePlan(BaseModel):
    schema_version: str = "1.2"
    animation_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,62}$")
    run_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
    calendar_name: str = "Calendar Animation Lab"
    timezone: str
    source_file: str | None = None
    clip_start_seconds: float | None = Field(default=None, ge=0)
    clip_end_seconds: float | None = Field(default=None, gt=0)
    clip_duration_seconds: float | None = Field(default=None, gt=0)
    output_fps: float | None = Field(default=None, gt=0)
    start_week: date
    frame_start: int = Field(ge=0)
    frame_count: int = Field(gt=0)
    mapping_mode: FrameMappingMode
    # Missing fields in persisted pre-compression plans retain their historical semantics.
    event_compression: EventCompressionMode = EventCompressionMode.NONE
    palette_preset: str | None = None
    background_color_id: str | None = None
    foreground_color_ids: list[str] = Field(default_factory=list)
    target_grid_width: int = Field(gt=0)
    target_grid_height: int = Field(gt=0)
    grid_profile: str = "legacy"
    slots_per_day: int | None = Field(default=None, gt=0)
    vertical_step_minutes: int | None = Field(default=None, gt=0)
    visible_start_hour: int | None = Field(default=None, ge=0, le=23)
    visible_end_hour: int | None = Field(default=None, ge=1, le=24)
    subcolumn_order_strategy: SubcolumnOrderStrategy
    subcolumn_order_keys: list[str] = Field(default_factory=list)
    max_events_per_frame: int = Field(gt=0)
    profile_ready: bool
    events_per_frame: list[int]
    total_events: int = Field(ge=0)
    frames: list[FrameUploadPlan]

    @model_validator(mode="after")
    def consistent_frames(self) -> "MultiFramePlan":
        if len(self.frames) != self.frame_count:
            raise ValueError("frame_count does not match frames")
        if len(self.events_per_frame) != self.frame_count:
            raise ValueError("frame_count does not match events_per_frame")
        if self.total_events != sum(self.events_per_frame):
            raise ValueError("total_events does not match events_per_frame")
        if [frame.planned_events for frame in self.frames] != self.events_per_frame:
            raise ValueError("frame planned event counts do not match events_per_frame")
        clip = (
            self.source_file,
            self.clip_start_seconds,
            self.clip_end_seconds,
            self.clip_duration_seconds,
            self.output_fps,
        )
        if any(value is not None for value in clip):
            if any(value is None for value in clip):
                raise ValueError("persisted source clip metadata must be complete")
            assert self.clip_start_seconds is not None
            assert self.clip_end_seconds is not None
            assert self.clip_duration_seconds is not None
            expected_end = self.clip_start_seconds + self.clip_duration_seconds
            if abs(self.clip_end_seconds - expected_end) > 1e-6:
                raise ValueError("clip end does not match start plus duration")
            if any(frame.source_timestamp_seconds is None for frame in self.frames):
                raise ValueError("persisted source timestamps must be complete")
        geometry = (
            self.slots_per_day,
            self.vertical_step_minutes,
            self.visible_start_hour,
            self.visible_end_hour,
        )
        if any(value is not None for value in geometry):
            if any(value is None for value in geometry):
                raise ValueError("persisted grid geometry must be complete")
            assert self.slots_per_day is not None
            assert self.vertical_step_minutes is not None
            assert self.visible_start_hour is not None
            assert self.visible_end_hour is not None
            if self.target_grid_width != self.slots_per_day * 7:
                raise ValueError("target grid width does not match slots_per_day")
            visible_minutes = (self.visible_end_hour - self.visible_start_hour) * 60
            if visible_minutes % self.vertical_step_minutes:
                raise ValueError("visible window is not divisible by vertical_step_minutes")
            if self.target_grid_height != visible_minutes // self.vertical_step_minutes:
                raise ValueError("target grid height does not match persisted time geometry")
        return self


class FrameUploadState(BaseModel):
    frame_index: int = Field(ge=0)
    status: FrameUploadStatus = FrameUploadStatus.PENDING
    planned_events: int = Field(ge=0)
    created_events: int = Field(default=0, ge=0)
    failed_events: int = Field(default=0, ge=0)
    errors: list[str] = Field(default_factory=list)
    created_event_ids: list[str] = Field(default_factory=list)
    event_retry_count: int = Field(default=0, ge=0)
    recovery_cycles: int = Field(default=0, ge=0)
    last_failure_retryable: bool | None = None
    rate_limit_exceeded_count: int = Field(default=0, ge=0)
    quota_exceeded_count: int = Field(default=0, ge=0)
    adaptive_rate_limit_cooldowns: int = Field(default=0, ge=0)
    quota_circuit_breaker_count: int = Field(default=0, ge=0)
    frame_started_at: datetime | None = None
    frame_completed_at: datetime | None = None
    duration_seconds: float | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def completed_state_is_truthful(self) -> "FrameUploadState":
        if self.status is FrameUploadStatus.COMPLETED and (
            self.created_events != self.planned_events or self.failed_events
        ):
            raise ValueError("completed frame must have all planned events and no failures")
        return self


class AnimationUploadState(BaseModel):
    schema_version: str = "1.0"
    run_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
    animation_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,62}$")
    calendar_id: str | None = None
    calendar_created: bool = False
    pause: UploadPauseMetadata | None = None
    pause_history: list[UploadPauseMetadata] = Field(default_factory=list)
    quota_wait: QuotaWaitState | None = None
    quota_wait_entries: int = Field(default=0, ge=0)
    quota_wait_total_seconds: float = Field(default=0.0, ge=0)
    quota_wait_attempts: int = Field(default=0, ge=0)
    quota_recoveries: int = Field(default=0, ge=0)
    largest_quota_cooldown_seconds: float = Field(default=0.0, ge=0)
    write_pacing: CalendarWritePacingSnapshot | None = None
    frames: list[FrameUploadState]
    updated_at: datetime

    @model_validator(mode="after")
    def unique_frame_indexes(self) -> "AnimationUploadState":
        indexes = [frame.frame_index for frame in self.frames]
        if len(indexes) != len(set(indexes)):
            raise ValueError("animation state contains duplicate frame indexes")
        return self

    def frame(self, frame_index: int) -> FrameUploadState:
        for frame in self.frames:
            if frame.frame_index == frame_index:
                return frame
        raise ValueError(f"animation state has no frame {frame_index}")


class FrameUploadExecutionResult(BaseModel):
    schema_version: str = "1.0"
    executed: bool
    run_id: str
    animation_id: str
    frame_index: int = Field(ge=0)
    status: FrameUploadStatus
    calendar_id: str | None = None
    planned_events: int = Field(ge=0)
    created_events: int = Field(default=0, ge=0)
    failed_events: int = Field(default=0, ge=0)
    created_event_ids: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    event_retry_count: int = Field(default=0, ge=0)
    recovery_cycles: int = Field(default=0, ge=0)
    last_failure_retryable: bool | None = None
    rate_limit_exceeded_count: int = Field(default=0, ge=0)
    quota_exceeded_count: int = Field(default=0, ge=0)
    adaptive_rate_limit_cooldowns: int = Field(default=0, ge=0)
    quota_circuit_breaker_count: int = Field(default=0, ge=0)
    pause: UploadPauseMetadata | None = None


class AnimationCleanupResult(BaseModel):
    selected_frames: list[int]
    matched_events: int = Field(ge=0)
    deleted_events: int = Field(ge=0)
    failed_events: int = Field(ge=0)
    errors: list[str] = Field(default_factory=list)
