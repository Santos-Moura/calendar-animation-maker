from pydantic import BaseModel, Field

from calendar_anim.calendar.frame_mapping.models import CellRole


class VerticalRun(BaseModel):
    logical_x: int = Field(ge=0)
    start_y: int = Field(ge=0)
    length: int = Field(gt=0)
    color_id: str
    cell_role: CellRole


class FrameVerticalCompressionEstimate(BaseModel):
    frame_index: int = Field(ge=0)
    baseline_events: int = Field(ge=0)
    compressed_runs: int = Field(ge=0)
    saved_events: int = Field(ge=0)
    reduction_percent: float = Field(ge=0, le=100)
    foreground_runs: int = Field(ge=0)
    background_runs: int = Field(ge=0)
    longest_vertical_run: int = Field(ge=0)
    average_run_length: float = Field(ge=0)
    runs: list[VerticalRun]


class AnimationVerticalCompressionEstimate(BaseModel):
    schema_version: str = "1.0"
    animation_id: str
    grid_width: int = Field(gt=0)
    grid_height: int = Field(gt=0)
    frames: list[FrameVerticalCompressionEstimate]
    total_baseline_events: int = Field(ge=0)
    total_compressed_runs: int = Field(ge=0)
    total_saved_events: int = Field(ge=0)
    total_reduction_percent: float = Field(ge=0, le=100)
    total_foreground_runs: int = Field(ge=0)
    total_background_runs: int = Field(ge=0)
    longest_vertical_run: int = Field(ge=0)
    average_run_length: float = Field(ge=0)
