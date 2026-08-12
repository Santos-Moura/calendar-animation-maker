import json
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, ValidationError

from calendar_anim.exceptions import CalendarAnimError


class CalendarLocalConfig(BaseModel):
    schema_version: str = "1.0"
    lab_calendar_id: str | None = None
    lab_calendar_name: str | None = None


class CalendarConfigStore:
    def __init__(self, path: Path = Path(".calendar-anim/calendar-config.json")) -> None:
        self.path = path

    def load(self) -> CalendarLocalConfig:
        if not self.path.exists():
            return CalendarLocalConfig()
        try:
            return CalendarLocalConfig.model_validate_json(self.path.read_text(encoding="utf-8"))
        except (OSError, ValidationError, json.JSONDecodeError) as error:
            raise CalendarAnimError(f"Invalid local Calendar configuration: {self.path}") from error

    def save(self, config: CalendarLocalConfig) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(config.model_dump_json(indent=2) + "\n", encoding="utf-8")


class CalendarConfigRepository(Protocol):
    def load(self) -> CalendarLocalConfig: ...

    def save(self, config: CalendarLocalConfig) -> None: ...
