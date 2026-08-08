from datetime import date
from typing import Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, computed_field, model_validator

from calendar_anim.calendar.models import CalendarEventDraft
from calendar_anim.calendar.subcolumn_ordering import (
    SUPPORTED_SUBCOLUMN_ORDER_STRATEGIES,
    SubcolumnOrderStrategy,
)

CalibrationPattern = Literal[
    "duration-scale",
    "overlap-columns",
    "color-palette",
    "position-grid",
    "horizontal-bars",
    "subcolumn-order",
    "combined",
]
SlotOrderStrategy = Literal[
    "creation-order",
    "summary-prefix",
    "stable-alternative",
    "unusable",
    "none",
]
OrderingControllingProperty = Literal["summary", "color_id", "unknown"]


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
    visual_order_forward: list[int] | None = None
    visual_order_reverse: list[int] | None = None
    visual_order_shuffled: list[int] | None = None
    stable_after_refresh: bool | None = None
    stable_after_navigation: bool | None = None
    stable_after_reopen: bool | None = None
    creation_order_controls_layout: bool | None = None
    recommended_slot_order_strategy: SlotOrderStrategy | None = None
    ordering_factor_tested: bool | None = None
    ordering_controlling_property: OrderingControllingProperty | None = None
    ordering_factor_stable: bool | None = None
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
        for field in (
            "visual_order_forward",
            "visual_order_reverse",
            "visual_order_shuffled",
        ):
            order = getattr(self, field)
            if order is not None and sorted(order) != list(range(6)):
                raise ValueError(f"{field} must contain each slot index from 0 to 5 exactly once")
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


class SubcolumnOrderMappingProfile(BaseModel):
    status: Literal["pending", "recorded"] = "pending"
    forward_creation_order: list[int] = Field(default_factory=lambda: list(range(6)))
    forward_visual_order: list[int] | None = None
    reverse_creation_order: list[int] = Field(default_factory=lambda: list(reversed(range(6))))
    reverse_visual_order: list[int] | None = None
    shuffled_creation_order: list[int] = Field(default_factory=lambda: [2, 5, 0, 4, 1, 3])
    shuffled_visual_order: list[int] | None = None
    stable_after_refresh: bool | None = None
    stable_after_navigation: bool | None = None
    stable_after_reopen: bool | None = None
    creation_order_controls_layout: bool | None = None
    recommended_slot_order_strategy: SlotOrderStrategy | None = None
    factor_tested: bool = False
    controlling_property: OrderingControllingProperty | None = None
    factor_stable: bool | None = None
    notes: str = ""

    @model_validator(mode="after")
    def validate_orders_and_status(self) -> "SubcolumnOrderMappingProfile":
        for field in (
            "forward_creation_order",
            "forward_visual_order",
            "reverse_creation_order",
            "reverse_visual_order",
            "shuffled_creation_order",
            "shuffled_visual_order",
        ):
            order = getattr(self, field)
            if order is not None and sorted(order) != list(range(6)):
                raise ValueError(f"{field} must contain each slot index from 0 to 5 exactly once")
        legacy_required = (
            self.forward_visual_order,
            self.reverse_visual_order,
            self.stable_after_refresh,
            self.stable_after_navigation,
            self.stable_after_reopen,
            self.creation_order_controls_layout,
            self.recommended_slot_order_strategy,
        )
        factor_required = (
            self.controlling_property,
            self.factor_stable,
            self.recommended_slot_order_strategy,
        )
        self.status = (
            "recorded"
            if all(value is not None for value in legacy_required)
            or (self.factor_tested and all(value is not None for value in factor_required))
            else "pending"
        )
        return self

    @property
    def recommended_strategy_supported(self) -> bool:
        if self.recommended_slot_order_strategy is None:
            return False
        return any(
            strategy.value == self.recommended_slot_order_strategy
            for strategy in SUPPORTED_SUBCOLUMN_ORDER_STRATEGIES
        )

    def strategy_ready(self, strategy: SubcolumnOrderStrategy) -> bool:
        if self.recommended_slot_order_strategy != strategy.value:
            return False
        if strategy is SubcolumnOrderStrategy.CREATION_ORDER:
            return (
                self.status == "recorded"
                and self.stable_after_refresh is True
                and self.stable_after_navigation is True
                and self.stable_after_reopen is True
                and self.creation_order_controls_layout is True
            )
        if strategy is SubcolumnOrderStrategy.SUMMARY_PREFIX:
            return (
                self.status == "recorded"
                and self.factor_tested
                and self.controlling_property == "summary"
                and self.factor_stable is True
            )
        return False

    @property
    def recommended_strategy_ready(self) -> bool:
        value = self.recommended_slot_order_strategy
        if value is None or not self.recommended_strategy_supported:
            return False
        strategy = SubcolumnOrderStrategy(value)
        return self.strategy_ready(strategy)


class CandidateGridProfile(BaseModel):
    width: int | None = Field(default=None, ge=1)
    height: int | None = Field(default=None, ge=1)


class CalibrationProfile(BaseModel):
    """Consolidated, local mapping from Calendar UI space to logical pixels."""

    schema_version: str = "1.2"
    calendar_ui: CalendarUIProfile = Field(default_factory=CalendarUIProfile)
    vertical_mapping: VerticalMappingProfile = Field(default_factory=VerticalMappingProfile)
    horizontal_mapping: HorizontalMappingProfile = Field(default_factory=HorizontalMappingProfile)
    color_mapping: ColorMappingProfile = Field(default_factory=ColorMappingProfile)
    position_mapping: PositionMappingProfile = Field(default_factory=PositionMappingProfile)
    horizontal_bar_mapping: HorizontalBarMappingProfile = Field(
        default_factory=HorizontalBarMappingProfile
    )
    subcolumn_order_mapping: SubcolumnOrderMappingProfile = Field(
        default_factory=SubcolumnOrderMappingProfile
    )
    candidate_grid: CandidateGridProfile = Field(default_factory=CandidateGridProfile)

    @model_validator(mode="after")
    def derive_logical_capacity(self) -> "CalibrationProfile":
        self.schema_version = "1.2"
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
    def missing_mapper_calibrations(self) -> list[str]:
        missing: list[str] = []
        vertical_ready = (
            self.vertical_mapping.minimum_visible_event_minutes is not None
            and self.vertical_mapping.minimum_distinguishable_height_minutes is not None
            and self.vertical_mapping.logical_rows is not None
        )
        if not vertical_ready:
            missing.append("vertical calibration")
        horizontal_ready = (
            self.horizontal_mapping.maximum_tested_overlap_columns is not None
            and self.horizontal_mapping.usable_overlap_columns_per_day is not None
            and self.horizontal_mapping.logical_columns is not None
        )
        if not horizontal_ready:
            missing.append("overlap-columns calibration")
        colors_ready = (
            bool(self.color_mapping.tested_color_ids)
            and bool(self.color_mapping.preferred_color_ids)
            and self.color_mapping.recommended_color_count is not None
        )
        if not colors_ready:
            missing.append("color-palette calibration")
        position_values = (
            self.position_mapping.week_alignment_ok,
            self.position_mapping.timezone_alignment_ok,
            self.position_mapping.day_alignment_ok,
            self.position_mapping.vertical_alignment_ok,
            self.position_mapping.week_starts_on,
        )
        position_ready = all(value is not None for value in position_values)
        if not position_ready:
            missing.append("position-grid calibration")
        bar_values = (
            self.horizontal_bar_mapping.independent_cells_appear_contiguous,
            self.horizontal_bar_mapping.visible_gaps_between_cells,
            self.horizontal_bar_mapping.same_color_cells_merge_visually,
            self.horizontal_bar_mapping.maximum_useful_bar_width,
            self.horizontal_bar_mapping.recommended_horizontal_strategy,
        )
        bars_ready = all(value is not None for value in bar_values)
        if not bars_ready:
            missing.append("horizontal-bars calibration")
        slot_order = self.subcolumn_order_mapping
        if not slot_order.recommended_strategy_ready:
            missing.append("subcolumn-order calibration")
        return missing

    @property
    def mapper_ready(self) -> bool:
        return not self.missing_mapper_calibrations

    @property
    def mapper_readiness(self) -> str:
        if self.mapper_ready:
            return "READY FOR SINGLE-FRAME EXPERIMENT"
        return "NOT READY"


class PatternDescription(BaseModel):
    name: CalibrationPattern
    description: str
    approximate_events: int
