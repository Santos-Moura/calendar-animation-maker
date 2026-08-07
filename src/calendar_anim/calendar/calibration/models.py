from datetime import date
from typing import Literal

from pydantic import BaseModel, Field, computed_field

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


class CalibrationObservations(BaseModel):
    schema_version: str = "1.0"
    run_id: str
    pattern: str | None = None
    calendar_ui: dict[str, str | int | bool | None]
    observations: dict[str, str | int | bool | None]


class PatternDescription(BaseModel):
    name: CalibrationPattern
    description: str
    approximate_events: int
