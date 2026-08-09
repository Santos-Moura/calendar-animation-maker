from pydantic import BaseModel, Field

from calendar_anim.calendar.frame_mapping.models import CellRole


class HorizontalBandSlot(BaseModel):
    subcolumn: int = Field(ge=0)
    color_id: str
    cell_role: CellRole


class SynchronizedHorizontalBand(BaseModel):
    day_offset: int = Field(ge=0, le=6)
    start_y: int = Field(ge=0)
    length: int = Field(gt=0)
    slots: list[HorizontalBandSlot]


class FrameHorizontalBandEstimate(BaseModel):
    frame_index: int = Field(ge=0)
    baseline_events: int = Field(ge=0)
    band_count: int = Field(ge=0)
    compressed_events: int = Field(ge=0)
    saved_events: int = Field(ge=0)
    reduction_percent: float = Field(ge=0, le=100)
    foreground_events: int = Field(ge=0)
    background_events: int = Field(ge=0)
    longest_band_rows: int = Field(ge=0)
    average_band_length: float = Field(ge=0)
    bands_per_day: list[int]
    bands: list[SynchronizedHorizontalBand]


class AnimationHorizontalBandEstimate(BaseModel):
    schema_version: str = "1.0"
    animation_id: str
    grid_width: int = Field(gt=0)
    grid_height: int = Field(gt=0)
    columns_per_day: int = Field(gt=0)
    days_used: int = Field(ge=1, le=7)
    frames: list[FrameHorizontalBandEstimate]
    total_baseline_events: int = Field(ge=0)
    total_compressed_events: int = Field(ge=0)
    total_saved_events: int = Field(ge=0)
    total_reduction_percent: float = Field(ge=0, le=100)
    total_bands: int = Field(ge=0)
    total_foreground_events: int = Field(ge=0)
    total_background_events: int = Field(ge=0)
    longest_band_rows: int = Field(ge=0)
    average_band_length: float = Field(ge=0)
