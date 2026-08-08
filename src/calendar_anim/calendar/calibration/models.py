from datetime import date
from typing import Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, computed_field, model_validator

from calendar_anim.calendar.models import CalendarEventDraft

CalibrationPattern = Literal[
    "duration-scale",
    "overlap-columns",
    "color-palette",
    "position-grid",
    "horizontal-bars",
    "combined",
]


class CalibrationPlan(BaseModel):
    schema_version: str = "1.0"
    pattern: CalibrationPattern
    animation_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,62}$")
    run_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
    calendar_name: str = Field(min_length=1)
    start_date: date
    timezone: str
    max_events: int = Field(ge=1, le=100)
    events: list[CalendarEventDraft]

    @computed_field  # type: ignore[prop-decorator]
    @property
    def event_count(self) -> int:
        return len(self.events)


class CalibrationExecutionResult(BaseModel):
    schema_version: str = "1.0"
    executed: bool
    run_id: str
    animation_id: str | None = None
    pattern: str | None = None
    calendar_id: str | None = None
    calendar_created: bool = False
    created_events: int = Field(default=0, ge=0)
    failed_events: int = Field(default=0, ge=0)
    created_event_ids: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


class CalendarUIProfile(BaseModel):
    """Calendar UI conditions under which a calibration was observed."""

    model_config = ConfigDict(extra="allow")

    view: str = "week"
    timezone: str = "America/Sao_Paulo"
    browser_zoom_percent: int | None = None
    viewport_width: int | None = None
    viewport_height: int | None = None
    sidebar_visible: bool = False
    weekends_visible: bool = True
    visible_start_hour: int = 6
    visible_end_hour: int = 18

    @model_validator(mode="after")
    def validate_visible_range(self) -> "CalendarUIProfile":
        if not 0 <= self.visible_start_hour < self.visible_end_hour <= 24:
            raise ValueError("visible hours must satisfy 0 <= start < end <= 24")
        return self


class CalibrationObservationValues(BaseModel):
    """Human observations, including fields written by older releases."""

    model_config = ConfigDict(extra="allow")

    minimum_visible_event_minutes: int | None = Field(default=None, ge=1)
    minimum_distinguishable_height_minutes: int | None = Field(default=None, ge=1)
    usable_overlap_columns: int | None = Field(default=None, ge=1)
    maximum_tested_overlap_columns: int | None = Field(default=None, ge=1)
    titles_visible: bool | None = None
    colors_distinguishable: bool | None = None
    tested_color_ids: list[str] | None = None
    preferred_color_ids: list[str] | None = None
    recommended_color_count: int | None = Field(default=None, ge=1)
    poor_contrast_color_ids: list[str] | None = None
    similar_color_groups: list[list[str]] | None = None
    week_alignment_ok: bool | None = None
    timezone_alignment_ok: bool | None = None
    day_alignment_ok: bool | None = None
    vertical_alignment_ok: bool | None = None
    week_starts_on: str | None = None
    independent_cells_appear_contiguous: bool | None = None
    visible_gaps_between_cells: bool | None = None
    same_color_cells_merge_visually: bool | None = None
    maximum_useful_bar_width: int | None = Field(default=None, ge=1)
    partial_bar_positioning_predictable: bool | None = None
    recommended_horizontal_strategy: str | None = None
    notes: str = ""

    # Compatibility with observation YAML written before the two vertical
    # measurements received distinct names.
    minimum_event_minutes: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def migrate_legacy_minimum(self) -> "CalibrationObservationValues":
        if self.minimum_visible_event_minutes is None and self.minimum_event_minutes is not None:
            self.minimum_visible_event_minutes = self.minimum_event_minutes
        if (
            self.usable_overlap_columns is not None
            and self.maximum_tested_overlap_columns is not None
            and self.usable_overlap_columns > self.maximum_tested_overlap_columns
        ):
            raise ValueError("usable overlap columns cannot exceed the maximum tested")
        return self


class CalibrationObservations(BaseModel):
    schema_version: str = "1.0"
    run_id: str
    pattern: str | None = None
    calendar_ui: CalendarUIProfile = Field(default_factory=CalendarUIProfile)
    observations: CalibrationObservationValues = Field(default_factory=CalibrationObservationValues)


class VerticalMappingProfile(BaseModel):
    minimum_visible_event_minutes: int | None = Field(default=None, ge=1)
    minimum_distinguishable_height_minutes: int | None = Field(default=None, ge=1)
    logical_rows: int | None = Field(default=None, ge=1)


class HorizontalMappingProfile(BaseModel):
    maximum_tested_overlap_columns: int | None = Field(default=None, ge=1)
    usable_overlap_columns_per_day: int | None = Field(
        default=None,
        ge=1,
        validation_alias=AliasChoices("usable_overlap_columns_per_day", "usable_overlap_columns"),
    )
    days_used: int = Field(default=7, ge=1, le=7)
    logical_columns: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def validate_measured_columns(self) -> "HorizontalMappingProfile":
        if (
            self.usable_overlap_columns_per_day is not None
            and self.maximum_tested_overlap_columns is not None
            and self.usable_overlap_columns_per_day > self.maximum_tested_overlap_columns
        ):
            raise ValueError("usable overlap columns cannot exceed the maximum tested")
        return self


class ColorMappingProfile(BaseModel):
    tested_color_ids: list[str] = Field(default_factory=list)
    preferred_color_ids: list[str] = Field(default_factory=list)
    recommended_color_count: int | None = Field(default=None, ge=1)
    poor_contrast_color_ids: list[str] = Field(default_factory=list)
    similar_color_groups: list[list[str]] = Field(default_factory=list)
    notes: str = ""

    @model_validator(mode="after")
    def validate_recommended_count(self) -> "ColorMappingProfile":
        if (
            self.preferred_color_ids
            and self.recommended_color_count is not None
            and self.recommended_color_count > len(self.preferred_color_ids)
        ):
            raise ValueError("recommended color count cannot exceed preferred color IDs")
        return self


class PositionMappingProfile(BaseModel):
    week_alignment_ok: bool | None = None
    timezone_alignment_ok: bool | None = None
    day_alignment_ok: bool | None = None
    vertical_alignment_ok: bool | None = None
    week_starts_on: str | None = None
    visible_start_hour: int | None = Field(default=None, ge=0, le=23)
    visible_end_hour: int | None = Field(default=None, ge=1, le=24)
    notes: str = ""

    @model_validator(mode="after")
    def validate_visible_hours(self) -> "PositionMappingProfile":
        if (
            self.visible_start_hour is not None
            and self.visible_end_hour is not None
            and self.visible_start_hour >= self.visible_end_hour
        ):
            raise ValueError("position visible start must be before visible end")
        return self


class HorizontalBarMappingProfile(BaseModel):
    independent_cells_appear_contiguous: bool | None = None
    visible_gaps_between_cells: bool | None = None
    same_color_cells_merge_visually: bool | None = None
    maximum_useful_bar_width: int | None = Field(default=None, ge=1)
    partial_bar_positioning_predictable: bool | None = None
    recommended_horizontal_strategy: str | None = None
    notes: str = ""


class CandidateGridProfile(BaseModel):
    width: int | None = Field(default=None, ge=1)
    height: int | None = Field(default=None, ge=1)


class CalibrationProfile(BaseModel):
    """Consolidated, local mapping from Calendar UI space to logical pixels."""

    schema_version: str = "1.1"
    calendar_ui: CalendarUIProfile = Field(default_factory=CalendarUIProfile)
    vertical_mapping: VerticalMappingProfile = Field(default_factory=VerticalMappingProfile)
    horizontal_mapping: HorizontalMappingProfile = Field(default_factory=HorizontalMappingProfile)
    color_mapping: ColorMappingProfile = Field(default_factory=ColorMappingProfile)
    position_mapping: PositionMappingProfile = Field(default_factory=PositionMappingProfile)
    horizontal_bar_mapping: HorizontalBarMappingProfile = Field(
        default_factory=HorizontalBarMappingProfile
    )
    candidate_grid: CandidateGridProfile = Field(default_factory=CandidateGridProfile)

    @model_validator(mode="after")
    def derive_logical_capacity(self) -> "CalibrationProfile":
        self.schema_version = "1.1"
        row_minutes = self.vertical_mapping.minimum_distinguishable_height_minutes
        if row_minutes is None:
            self.vertical_mapping.logical_rows = None
        else:
            visible_minutes = (
                self.calendar_ui.visible_end_hour - self.calendar_ui.visible_start_hour
            ) * 60
            self.vertical_mapping.logical_rows = visible_minutes // row_minutes

        usable_columns = self.horizontal_mapping.usable_overlap_columns_per_day
        if usable_columns is None:
            self.horizontal_mapping.logical_columns = None
        else:
            self.horizontal_mapping.logical_columns = (
                self.horizontal_mapping.days_used * usable_columns
            )
        self.candidate_grid.width = self.horizontal_mapping.logical_columns
        self.candidate_grid.height = self.vertical_mapping.logical_rows
        return self

    @property
    def mapper_ready(self) -> bool:
        vertical_ready = (
            self.vertical_mapping.minimum_visible_event_minutes is not None
            and self.vertical_mapping.minimum_distinguishable_height_minutes is not None
            and self.vertical_mapping.logical_rows is not None
        )
        horizontal_ready = (
            self.horizontal_mapping.maximum_tested_overlap_columns is not None
            and self.horizontal_mapping.usable_overlap_columns_per_day is not None
            and self.horizontal_mapping.logical_columns is not None
        )
        colors_ready = (
            bool(self.color_mapping.tested_color_ids)
            and bool(self.color_mapping.preferred_color_ids)
            and self.color_mapping.recommended_color_count is not None
        )
        position_values = (
            self.position_mapping.week_alignment_ok,
            self.position_mapping.timezone_alignment_ok,
            self.position_mapping.day_alignment_ok,
            self.position_mapping.vertical_alignment_ok,
            self.position_mapping.week_starts_on,
        )
        position_ready = all(value is not None for value in position_values)
        bar_values = (
            self.horizontal_bar_mapping.independent_cells_appear_contiguous,
            self.horizontal_bar_mapping.visible_gaps_between_cells,
            self.horizontal_bar_mapping.same_color_cells_merge_visually,
            self.horizontal_bar_mapping.maximum_useful_bar_width,
            self.horizontal_bar_mapping.recommended_horizontal_strategy,
        )
        bars_ready = all(value is not None for value in bar_values)
        return all((vertical_ready, horizontal_ready, colors_ready, position_ready, bars_ready))

    @property
    def mapper_readiness(self) -> str:
        if self.mapper_ready:
            return "READY FOR SINGLE-FRAME EXPERIMENT"
        return "NOT READY"


class PatternDescription(BaseModel):
    name: CalibrationPattern
    description: str
    approximate_events: int
