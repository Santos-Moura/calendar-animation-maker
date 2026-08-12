from pathlib import Path

from pydantic import BaseModel, Field, field_validator


class CalendarAccountProfile(BaseModel):
    schema_version: str = "1.0"
    profile_name: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,62}$")
    credentials_file: Path
    token_file: Path
    calendar_id: str | None = None
    calendar_name: str
    timezone: str = "America/Sao_Paulo"
    description: str | None = None
    authenticated_google_account: str | None = None
    browser_profile_directory: Path
    capture_zoom_percent: int = Field(default=33, ge=25, le=500)
    legacy_compatible: bool = False

    @field_validator("token_file", "browser_profile_directory")
    @classmethod
    def private_runtime_paths(cls, value: Path) -> Path:
        if not value.parts:
            raise ValueError("profile runtime paths may not be empty")
        if ".." in value.parts:
            raise ValueError("profile runtime paths may not escape the workspace")
        return value


class CalendarProfileInspection(BaseModel):
    profile_name: str
    credentials_file: Path
    credentials_present: bool
    token_file: Path
    token_present: bool
    authenticated_google_account: str | None = None
    calendar_id: str | None = None
    calendar_name: str
    timezone: str
    calendar_exists: bool | None = None
    calendar_access_role: str | None = None
    browser_profile_directory: Path
    capture_zoom_percent: int
