from datetime import date, datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field, computed_field, model_validator

from calendar_anim.calendar.models import CalendarEventDraft
from calendar_anim.calendar.subcolumn_ordering import SubcolumnOrderStrategy

FitMode = Literal["contain"]


class FrameMappingMode(StrEnum):
    SPARSE = "sparse"
    FULL_GRID = "full-grid"


class EventCompressionMode(StrEnum):
    NONE = "none"
    SYNCHRONIZED_HORIZONTAL_BANDS = "synchronized-horizontal-bands"


DEFAULT_EVENT_COMPRESSION = EventCompressionMode.SYNCHRONIZED_HORIZONTAL_BANDS


class CellRole(StrEnum):
    FOREGROUND = "foreground"
    BACKGROUND = "background"


class LogicalCell(BaseModel):
    """One foreground or structural background cell in a logical canvas."""

    x: int = Field(ge=0)
    y: int = Field(ge=0)
    source_x: int | None = Field(default=None, ge=0)
    source_y: int | None = Field(default=None, ge=0)
    color_hex: str = Field(pattern=r"^#[0-9A-Fa-f]{6}$")
    source_block_index: int | None = Field(default=None, ge=0)
    cell_role: CellRole = CellRole.FOREGROUND


class CalendarMappedCell(BaseModel):
    """A fitted cell with its intended Calendar coordinates and color."""

    logical_x: int = Field(ge=0)
    logical_y: int = Field(ge=0)
    source_x: int | None = Field(default=None, ge=0)
    source_y: int | None = Field(default=None, ge=0)
    day_offset: int = Field(ge=0, le=6)
    subcolumn: int = Field(ge=0)
    start: datetime
    end: datetime
    color_id: str
    color_hex: str = Field(pattern=r"^#[0-9A-Fa-f]{6}$")
    source_block_index: int | None = Field(default=None, ge=0)
    cell_role: CellRole = CellRole.FOREGROUND

    @model_validator(mode="after")
    def valid_interval(self) -> "CalendarMappedCell":
        if self.start.tzinfo is None or self.end.tzinfo is None:
            raise ValueError("mapped cell datetimes must be timezone-aware")
        if self.start >= self.end:
            raise ValueError("mapped cell start must be before end")
        return self


class FrameMappingStatistics(BaseModel):
    source_blocks: int = Field(ge=0)
    expanded_logical_cells: int = Field(ge=0)
    non_background_cells: int = Field(ge=0)
    mapped_cells: int = Field(ge=0)
    calendar_events: int = Field(ge=0)
    unique_calendar_colors: int = Field(ge=0)
    cells_per_event: float = Field(ge=0)
    compression_ratio: float = Field(ge=0)
    foreground_cells_after_fitting: int = Field(default=0, ge=0)
    background_structural_cells: int = Field(default=0, ge=0)
    total_logical_cells: int = Field(default=0, ge=0)
    foreground_events: int = Field(default=0, ge=0)
    background_events: int = Field(default=0, ge=0)
    foreground_calendar_colors: int = Field(default=0, ge=0)
    sparse_event_estimate: int = Field(default=0, ge=0)
    full_grid_event_estimate: int = Field(default=0, ge=0)
    baseline_calendar_events: int = Field(default=0, ge=0)
    saved_calendar_events: int = Field(default=0, ge=0)
    synchronized_horizontal_bands: int = Field(default=0, ge=0)


class SingleFrameCalendarPlan(BaseModel):
    schema_version: str = "1.2"
    animation_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,62}$")
    run_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
    calendar_name: str = "Calendar Animation Lab"
    frame_index: int = Field(ge=0)
    timezone: str
    week_start_date: date
    source_grid_width: int = Field(gt=0)
    source_grid_height: int = Field(gt=0)
    target_grid_width: int = Field(gt=0)
    target_grid_height: int = Field(gt=0)
    columns_per_day: int = Field(default=1, gt=0)
    days_used: int = Field(default=7, ge=1, le=7)
    fit: FitMode = "contain"
    mapping_mode: FrameMappingMode = FrameMappingMode.SPARSE
    # Missing fields in persisted pre-compression plans retain their historical semantics.
    event_compression: EventCompressionMode = EventCompressionMode.NONE
    background_color_id: str | None = None
    palette_preset: str | None = None
    foreground_color_ids: list[str] = Field(default_factory=list)
    profile_ready: bool
    horizontal_strategy: str
    subcolumn_order_strategy: SubcolumnOrderStrategy = SubcolumnOrderStrategy.NONE
    subcolumn_order_keys: list[str] = Field(default_factory=list)
    max_execute_events: int = Field(gt=0)
    warnings: list[str] = Field(default_factory=list)
    statistics: FrameMappingStatistics
    mapped_cells: list[CalendarMappedCell]
    events: list[CalendarEventDraft]

    @computed_field  # type: ignore[prop-decorator]
    @property
    def event_count(self) -> int:
        return len(self.events)


class SingleFrameExecutionResult(BaseModel):
    schema_version: str = "1.0"
    executed: bool
    run_id: str
    animation_id: str
    frame_index: int = Field(ge=0)
    calendar_id: str | None = None
    calendar_created: bool = False
    planned_events: int = Field(ge=0)
    created_events: int = Field(default=0, ge=0)
    failed_events: int = Field(default=0, ge=0)
    foreground_created: int = Field(default=0, ge=0)
    background_created: int = Field(default=0, ge=0)
    created_event_ids: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
