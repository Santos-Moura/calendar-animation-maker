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


class CalibrationProfile(BaseModel):
    """Consolidated, local mapping from Calendar UI space to logical pixels."""

    schema_version: str = "1.0"
    calendar_ui: CalendarUIProfile = Field(default_factory=CalendarUIProfile)
    vertical_mapping: VerticalMappingProfile = Field(default_factory=VerticalMappingProfile)
    horizontal_mapping: HorizontalMappingProfile = Field(default_factory=HorizontalMappingProfile)

    @model_validator(mode="after")
    def derive_logical_capacity(self) -> "CalibrationProfile":
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
        return self


class PatternDescription(BaseModel):
    name: CalibrationPattern
    description: str
    approximate_events: int
