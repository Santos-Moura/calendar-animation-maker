from datetime import date, datetime
from enum import StrEnum

from pydantic import BaseModel, Field, model_validator


class HybridOutputMode(StrEnum):
    PIXEL_FAITHFUL = "pixel_faithful"
    HEADER_PRESERVED_LETTERBOX = "header_preserved_letterbox"
    HEADER_PRESERVED_FILL = "header_preserved_fill"

    @property
    def directory_name(self) -> str:
        return self.value.replace("_", "-")

    @property
    def includes_header(self) -> bool:
        return self is not HybridOutputMode.PIXEL_FAITHFUL


class HybridFrameStatus(StrEnum):
    PENDING = "pending"
    CAPTURING = "capturing"
    COMPLETED = "completed"
    FAILED = "failed"


class HybridFramePlan(BaseModel):
    frame_index: int = Field(ge=0, le=107)
    human_frame: int = Field(ge=1, le=108)
    week_start: date
    calendar_profile: str
    calendar_name: str
    capture_zoom_percent: int
    expected_occurrences: int = Field(ge=0)
    source_frame_plan: str


class HybridCapturePlan(BaseModel):
    schema_version: str = "1.0"
    run_id: str
    source_run_id: str
    source_sha256: str
    frame_count: int = 108
    fps: float = 3.0
    grid_width: int = 126
    grid_height: int = 72
    normalized_width: int = 504
    normalized_height: int = 288
    clip_start_seconds: float = 114.0
    clip_end_seconds: float = 150.0
    frames: list[HybridFramePlan]

    @model_validator(mode="after")
    def exact_sequence(self) -> "HybridCapturePlan":
        indexes = [frame.frame_index for frame in self.frames]
        if indexes != list(range(108)):
            raise ValueError("hybrid capture must account for frame indices 0-107 exactly once")
        if [frame.human_frame for frame in self.frames] != list(range(1, 109)):
            raise ValueError("hybrid human frames must be 1-108 exactly once")
        for frame in self.frames:
            expected_profile = "account-a" if frame.frame_index <= 22 else "account-b"
            expected_zoom = 33 if frame.frame_index <= 22 else 90
            if (
                frame.calendar_profile != expected_profile
                or frame.capture_zoom_percent != expected_zoom
            ):
                raise ValueError("hybrid profile/zoom boundary differs at frame index 23")
        return self


class HybridFrameState(BaseModel):
    frame_index: int
    status: HybridFrameStatus = HybridFrameStatus.PENDING
    profile: str
    started_at: datetime | None = None
    completed_at: datetime | None = None
    error: str | None = None


class HybridCaptureState(BaseModel):
    schema_version: str = "2.0"
    run_id: str
    output_mode: HybridOutputMode = HybridOutputMode.PIXEL_FAITHFUL
    frames: list[HybridFrameState]
    updated_at: datetime

    def frame(self, frame_index: int) -> HybridFrameState:
        return next(item for item in self.frames if item.frame_index == frame_index)


class SanityFrameResult(BaseModel):
    frame_index: int
    human_frame: int
    profile: str
    week_start: date
    expected_occurrences: int
    rendered_dom_events: int
    capture_success: bool
    capture_load_success: bool = True
    capture_error: str | None = None
    capture_retry_cycles: int = 0
    capture_timestamp: datetime | None = None
    navigation_complete: bool = False
    stabilization_seconds: float = 0
    raw_dom_nodes: int = 0
    unique_event_chips: int = 0
    dom_population_samples: list[dict[str, object]] = Field(default_factory=list)
    normalized_width: int
    normalized_height: int
    logical_cell_width: float
    logical_cell_height: float
    grid_left: float = 0
    grid_top: float = 0
    grid_right: float = 0
    grid_bottom: float = 0
    expected_color_distribution: dict[str, int]
    rendered_color_distribution: dict[str, int]
    logical_cell_match_ratio: float = Field(ge=0, le=1)
    obvious_missing_content: bool
    obvious_color_mismatch: bool
    obvious_ordering_issue: bool
    unique_event_population_valid: bool = True
    grid_geometry_valid: bool = True
    colors_valid: bool = True
    ordering_valid: bool = True
    visual_match_valid: bool = True
    raw_artifact: str
    logical_artifact: str
    normalized_artifact: str
    expected_artifact: str


class HybridSanityReport(BaseModel):
    schema_version: str = "1.0"
    run_id: str
    profile: str = "account-b"
    calendar: str = "Calendar Animation Lab B"
    frames_checked: list[int]
    results: list[SanityFrameResult]
    automated_result: str
    visual_approval_required: bool = True
    google_calendar_writes: bool = False


class HybridSeamReport(BaseModel):
    schema_version: str = "1.0"
    run_id: str
    account_a_frame_index: int = 22
    account_b_frame_index: int = 23
    account_a_zoom: int = 33
    account_b_zoom: int = 90
    normalized_width: int = 504
    normalized_height: int = 288
    cell_width_relative_delta: float = Field(ge=0)
    cell_height_relative_delta: float = Field(ge=0)
    geometry_result: str
    visual_approval_required: bool = True
    google_calendar_writes: bool = False
