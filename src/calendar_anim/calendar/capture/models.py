from datetime import date, datetime
from enum import StrEnum
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class FrameCaptureStatus(StrEnum):
    PENDING = "pending"
    CAPTURING = "capturing"
    COMPLETED = "completed"
    FAILED = "failed"


class CalendarCaptureConfig(BaseModel):
    viewport_width: int = Field(default=1920, gt=0)
    viewport_height: int = Field(default=1080, gt=0)
    device_scale_factor: float = Field(default=1.0, gt=0)
    browser_zoom_percent: int = Field(default=100, ge=25, le=500)
    color_scheme: Literal["dark", "light"] = "dark"
    calendar_view: Literal["week"] = "week"
    sidebar_hidden: bool = True
    visible_start_hour: int = Field(default=6, ge=0, le=23)
    visible_end_hour: int = Field(default=18, ge=1, le=24)
    ready_timeout_seconds: float = Field(default=30.0, gt=0)
    stabilization_seconds: float = Field(default=2.0, ge=0)
    stable_snapshot_count: int = Field(default=2, ge=1, le=10)
    profile_directory: Path = Path(".calendar-anim/browser-profile")

    @model_validator(mode="after")
    def valid_visible_window(self) -> "CalendarCaptureConfig":
        if self.visible_end_hour <= self.visible_start_hour:
            raise ValueError("visible_end_hour must be after visible_start_hour")
        return self


class CaptureFramePlan(BaseModel):
    frame_index: int = Field(ge=0)
    week_start: date
    planned_events: int = Field(ge=0)
    screenshot_path: str = Field(pattern=r"^frames/frame-[0-9]{4,}\.png$")


class CapturePlan(BaseModel):
    schema_version: str = "1.0"
    run_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
    animation_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,62}$")
    source_plan_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    frame_count: int = Field(gt=0)
    config: CalendarCaptureConfig
    frames: list[CaptureFramePlan]

    @model_validator(mode="after")
    def consistent_frames(self) -> "CapturePlan":
        if len(self.frames) != self.frame_count:
            raise ValueError("frame_count does not match capture frames")
        indexes = [frame.frame_index for frame in self.frames]
        if len(indexes) != len(set(indexes)):
            raise ValueError("capture plan contains duplicate frame indexes")
        return self


class FrameCaptureState(BaseModel):
    frame_index: int = Field(ge=0)
    status: FrameCaptureStatus = FrameCaptureStatus.PENDING
    screenshot_path: str
    started_at: datetime | None = None
    completed_at: datetime | None = None
    error: str | None = None


class CaptureState(BaseModel):
    schema_version: str = "1.0"
    run_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
    source_plan_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    frames: list[FrameCaptureState]
    updated_at: datetime

    @model_validator(mode="after")
    def unique_frame_indexes(self) -> "CaptureState":
        indexes = [frame.frame_index for frame in self.frames]
        if len(indexes) != len(set(indexes)):
            raise ValueError("capture state contains duplicate frame indexes")
        return self

    def frame(self, frame_index: int) -> FrameCaptureState:
        for frame in self.frames:
            if frame.frame_index == frame_index:
                return frame
        raise ValueError(f"capture state has no frame {frame_index}")
