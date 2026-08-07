from datetime import datetime

from pydantic import BaseModel, Field, model_validator


class CalendarEventDraft(BaseModel):
    frame_index: int | None = None
    block_index: int | None = None
    start: datetime
    end: datetime
    color_id: str | None = None
    color_hex: str | None = Field(default=None, pattern=r"^#[0-9A-Fa-f]{6}$")
    summary: str
    private_metadata: dict[str, str]

    @model_validator(mode="after")
    def valid_interval(self) -> "CalendarEventDraft":
        if self.start.tzinfo is None or self.end.tzinfo is None:
            raise ValueError("calendar event datetimes must be timezone-aware")
        if self.start >= self.end:
            raise ValueError("calendar event start must be before end")
        return self


class CalendarPlan(BaseModel):
    schema_version: str = "1.0"
    animation_id: str
    timezone: str
    events: list[CalendarEventDraft]


class CalendarInfo(BaseModel):
    id: str
    name: str
    description: str = ""
    timezone: str
    primary: bool = False


class CalendarColor(BaseModel):
    id: str
    background: str = Field(pattern=r"^#[0-9A-Fa-f]{6}$")
    foreground: str = Field(pattern=r"^#[0-9A-Fa-f]{6}$")


class CalendarEventInfo(BaseModel):
    id: str
    summary: str
    start: datetime
    end: datetime
    private_metadata: dict[str, str]


class CalendarWriteResult(BaseModel):
    created_event_ids: list[str] = Field(default_factory=list)
    created_event_indexes: list[int] = Field(default_factory=list)
    failed_events: int = Field(default=0, ge=0)
    errors: list[str] = Field(default_factory=list)

    @property
    def created_events(self) -> int:
        return len(self.created_event_ids)


class CalendarDeleteResult(BaseModel):
    deleted_events: int = Field(default=0, ge=0)
    failed_events: int = Field(default=0, ge=0)
    errors: list[str] = Field(default_factory=list)
