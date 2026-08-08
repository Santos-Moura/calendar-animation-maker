from datetime import date, datetime
from enum import StrEnum

from pydantic import BaseModel, Field, model_validator

from calendar_anim.calendar.frame_mapping.models import FrameMappingMode
from calendar_anim.calendar.subcolumn_ordering import SubcolumnOrderStrategy


class FrameUploadStatus(StrEnum):
    PENDING = "pending"
    UPLOADING = "uploading"
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"


class FrameUploadPlan(BaseModel):
    frame_index: int = Field(ge=0)
    week_start: date
    frame_run_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
    planned_events: int = Field(ge=0)
    artifact_directory: str


class MultiFramePlan(BaseModel):
    schema_version: str = "1.0"
    animation_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,62}$")
    run_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
    calendar_name: str = "Calendar Animation Lab"
    timezone: str
    start_week: date
    frame_start: int = Field(ge=0)
    frame_count: int = Field(gt=0)
    mapping_mode: FrameMappingMode
    target_grid_width: int = Field(gt=0)
    target_grid_height: int = Field(gt=0)
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
        return self


class FrameUploadState(BaseModel):
    frame_index: int = Field(ge=0)
    status: FrameUploadStatus = FrameUploadStatus.PENDING
    planned_events: int = Field(ge=0)
    created_events: int = Field(default=0, ge=0)
    failed_events: int = Field(default=0, ge=0)
    errors: list[str] = Field(default_factory=list)
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


class AnimationCleanupResult(BaseModel):
    selected_frames: list[int]
    matched_events: int = Field(ge=0)
    deleted_events: int = Field(ge=0)
    failed_events: int = Field(ge=0)
    errors: list[str] = Field(default_factory=list)
