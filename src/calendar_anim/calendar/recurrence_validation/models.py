from datetime import date, datetime
from enum import StrEnum

from pydantic import BaseModel, Field, model_validator


class ValidationResourceRole(StrEnum):
    RECURRING_PARENT = "recurring-parent"
    STANDALONE_CONTROL = "standalone-control"


class ValidationStatus(StrEnum):
    PENDING = "pending"
    PARTIAL = "partial"
    COMPLETED = "completed"


class ValidationVisualProperties(BaseModel):
    summary: str
    summary_codepoints: str
    color_id: str
    local_start_time: str
    duration_seconds: int = Field(gt=0)
    timezone: str
    transparency: str = "opaque"
    visibility: str = "default"
    event_type: str = "default"


class ValidationResourcePlan(BaseModel):
    event_id: str
    role: ValidationResourceRole
    pair_index: int | None = Field(default=None, ge=0, le=2)
    week_start: date
    start: datetime
    end: datetime
    summary: str
    color_id: str
    timezone: str
    recurrence: list[str] = Field(default_factory=list)
    private_metadata: dict[str, str]

    @model_validator(mode="after")
    def validate_interval_and_recurrence(self) -> "ValidationResourcePlan":
        if self.start.tzinfo is None or self.end.tzinfo is None:
            raise ValueError("validation event datetimes must be timezone-aware")
        if self.start >= self.end:
            raise ValueError("validation event start must be before end")
        recurring = self.role is ValidationResourceRole.RECURRING_PARENT
        if recurring != bool(self.recurrence):
            raise ValueError("only the recurring parent may contain recurrence rules")
        return self

    def google_body(self) -> dict[str, object]:
        body: dict[str, object] = {
            "id": self.event_id,
            "summary": self.summary,
            "colorId": self.color_id,
            "start": {
                "dateTime": self.start.isoformat(),
                "timeZone": self.timezone,
            },
            "end": {
                "dateTime": self.end.isoformat(),
                "timeZone": self.timezone,
            },
            "extendedProperties": {"private": self.private_metadata},
        }
        if self.recurrence:
            body["recurrence"] = self.recurrence
        return body


class ValidationWeek(BaseModel):
    pair_index: int = Field(ge=0, le=2)
    variant: str = Field(pattern=r"^(recurring|standalone)$")
    week_start: date
    expected_events: int = 1


class RecurrenceValidationPlan(BaseModel):
    schema_version: str = "1.0"
    validation_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
    source_run_id: str
    source_frame_index: int = Field(ge=0)
    source_event_index: int = Field(ge=0)
    calendar_name: str
    timezone: str
    visual_properties: ValidationVisualProperties
    weeks: list[ValidationWeek]
    resources: list[ValidationResourcePlan]
    recurring_instance_count: int = 3
    rdate_value_count: int = 2
    standalone_count: int = 3
    expected_events_insert_calls: int = 4
    displayed_week_count: int = 6
    google_calendar_writes: bool = False

    @model_validator(mode="after")
    def validate_smallest_scope(self) -> "RecurrenceValidationPlan":
        recurring = [
            item for item in self.resources if item.role is ValidationResourceRole.RECURRING_PARENT
        ]
        standalone = [
            item
            for item in self.resources
            if item.role is ValidationResourceRole.STANDALONE_CONTROL
        ]
        if len(recurring) != 1 or len(standalone) != 3 or len(self.resources) != 4:
            raise ValueError("validation must contain one recurring parent and three controls")
        if len(self.weeks) != 6 or len({week.week_start for week in self.weeks}) != 6:
            raise ValueError("validation must cover six unique weeks")
        parent = recurring[0]
        if len(parent.recurrence) != 1 or not parent.recurrence[0].startswith("RDATE"):
            raise ValueError("recurring parent must contain exactly one RDATE property")
        comparable = {
            (item.summary, item.color_id, item.timezone, item.end - item.start)
            for item in self.resources
        }
        if len(comparable) != 1:
            raise ValueError("all validation resources must have identical visual properties")
        return self

    @property
    def first_week(self) -> date:
        return min(week.week_start for week in self.weeks)

    @property
    def last_week(self) -> date:
        return max(week.week_start for week in self.weeks)


class ValidationUploadState(BaseModel):
    schema_version: str = "1.0"
    validation_id: str
    status: ValidationStatus = ValidationStatus.PENDING
    calendar_id: str | None = None
    created_resource_ids: list[str] = Field(default_factory=list)
    reconciled_resource_ids: list[str] = Field(default_factory=list)
    events_insert_calls: int = Field(default=0, ge=0)
    rate_limit_exceeded_count: int = Field(default=0, ge=0)
    quota_exceeded_count: int = Field(default=0, ge=0)
    errors: list[str] = Field(default_factory=list)
    updated_at: datetime


class ValidationCleanupResult(BaseModel):
    validation_id: str
    matched_resource_ids: list[str]
    deleted_resources: int = Field(default=0, ge=0)
    failed_deletions: int = Field(default=0, ge=0)
    errors: list[str] = Field(default_factory=list)
