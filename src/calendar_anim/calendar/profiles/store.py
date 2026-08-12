import json
import os
from pathlib import Path

from pydantic import ValidationError

from calendar_anim.calendar.google_auth import GoogleOAuthConfig
from calendar_anim.calendar.local_config import CalendarConfigStore, CalendarLocalConfig
from calendar_anim.calendar.profiles.models import CalendarAccountProfile
from calendar_anim.exceptions import CalendarAnimError

DEFAULT_PROFILE_NAME = "account-a"
DEFAULT_SECONDARY_PROFILE_NAME = "account-b"
DEFAULT_CALENDAR_NAME = "Calendar Animation Lab"
DEFAULT_SECONDARY_CALENDAR_NAME = "Calendar Animation Lab B"
DEFAULT_ACCOUNT_A_CAPTURE_ZOOM = 33
DEFAULT_ACCOUNT_B_CAPTURE_ZOOM = 90


def default_capture_zoom(profile_name: str) -> int:
    if profile_name == DEFAULT_SECONDARY_PROFILE_NAME:
        return DEFAULT_ACCOUNT_B_CAPTURE_ZOOM
    return DEFAULT_ACCOUNT_A_CAPTURE_ZOOM


class CalendarProfileStore:
    def __init__(
        self,
        root: Path = Path(".calendar-anim/profiles"),
        legacy_calendar_config: Path = Path(".calendar-anim/calendar-config.json"),
    ) -> None:
        self.root = root
        self.legacy_calendar_config = legacy_calendar_config

    @staticmethod
    def validate_name(profile_name: str) -> str:
        try:
            CalendarAccountProfile(
                profile_name=profile_name,
                credentials_file=Path("credentials.json"),
                token_file=Path("token.json"),
                calendar_name="placeholder",
                browser_profile_directory=Path(".calendar-anim/browser-profile"),
            )
        except ValidationError as error:
            raise CalendarAnimError(f"Invalid Calendar profile: {profile_name!r}") from error
        return profile_name

    def directory(self, profile_name: str) -> Path:
        return self.root / self.validate_name(profile_name)

    def profile_path(self, profile_name: str) -> Path:
        return self.directory(profile_name) / "profile.json"

    def token_path(self, profile_name: str) -> Path:
        if profile_name == DEFAULT_PROFILE_NAME:
            return GoogleOAuthConfig().token_file
        return self.directory(profile_name) / "token.json"

    def browser_profile_directory(self, profile_name: str) -> Path:
        if profile_name == DEFAULT_PROFILE_NAME:
            return Path(".calendar-anim/browser-profile")
        return Path(".calendar-anim/browser-profiles") / profile_name

    def initialize(
        self,
        profile_name: str,
        *,
        calendar_name: str | None = None,
        timezone: str = "America/Sao_Paulo",
        description: str | None = None,
        credentials_file: Path | None = None,
    ) -> CalendarAccountProfile:
        existing = self.load_optional(profile_name)
        if existing is not None:
            expected_name = calendar_name or existing.calendar_name
            if existing.calendar_name != expected_name or existing.timezone != timezone:
                raise CalendarAnimError(
                    f"Profile {profile_name!r} already exists with different settings"
                )
            self.save(existing)
            return existing
        default_name = (
            DEFAULT_CALENDAR_NAME
            if profile_name == DEFAULT_PROFILE_NAME
            else DEFAULT_SECONDARY_CALENDAR_NAME
        )
        oauth = GoogleOAuthConfig(credentials_file=credentials_file)
        profile = CalendarAccountProfile(
            profile_name=profile_name,
            credentials_file=oauth.credentials_file,
            token_file=self.token_path(profile_name),
            calendar_name=calendar_name or default_name,
            timezone=timezone,
            description=description,
            browser_profile_directory=self.browser_profile_directory(profile_name),
            capture_zoom_percent=default_capture_zoom(profile_name),
            legacy_compatible=profile_name == DEFAULT_PROFILE_NAME,
        )
        self.save(profile)
        return profile

    def load(self, profile_name: str) -> CalendarAccountProfile:
        profile = self.load_optional(profile_name)
        if profile is None:
            raise CalendarAnimError(
                f"Calendar profile does not exist: {profile_name}. Authenticate or initialize it."
            )
        return profile

    def load_optional(self, profile_name: str) -> CalendarAccountProfile | None:
        path = self.profile_path(profile_name)
        if path.is_file():
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                payload.setdefault("capture_zoom_percent", default_capture_zoom(profile_name))
                return CalendarAccountProfile.model_validate(payload)
            except (OSError, ValidationError, ValueError) as error:
                raise CalendarAnimError(f"Invalid Calendar profile: {path}") from error
        if profile_name != DEFAULT_PROFILE_NAME:
            return None
        legacy = CalendarConfigStore(self.legacy_calendar_config).load()
        oauth = GoogleOAuthConfig()
        return CalendarAccountProfile(
            profile_name=DEFAULT_PROFILE_NAME,
            credentials_file=oauth.credentials_file,
            token_file=oauth.token_file,
            calendar_id=legacy.lab_calendar_id,
            calendar_name=legacy.lab_calendar_name or DEFAULT_CALENDAR_NAME,
            timezone="America/Sao_Paulo",
            browser_profile_directory=self.browser_profile_directory(DEFAULT_PROFILE_NAME),
            capture_zoom_percent=DEFAULT_ACCOUNT_A_CAPTURE_ZOOM,
            legacy_compatible=True,
        )

    def save(self, profile: CalendarAccountProfile) -> Path:
        path = self.profile_path(profile.profile_name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(profile.model_dump_json(indent=2) + "\n", encoding="utf-8")
        if profile.profile_name == DEFAULT_PROFILE_NAME:
            CalendarConfigStore(self.legacy_calendar_config).save(
                CalendarLocalConfig(
                    lab_calendar_id=profile.calendar_id,
                    lab_calendar_name=profile.calendar_name,
                )
            )
        return path

    def list_profiles(self) -> list[CalendarAccountProfile]:
        names = {DEFAULT_PROFILE_NAME}
        if self.root.is_dir():
            names.update(
                child.name
                for child in self.root.iterdir()
                if child.is_dir() and (child / "profile.json").is_file()
            )
        return [self.load(name) for name in sorted(names)]

    def oauth_config(self, profile: CalendarAccountProfile) -> GoogleOAuthConfig:
        return GoogleOAuthConfig(
            credentials_file=profile.credentials_file,
            token_file=profile.token_file,
        )


class ProfileCalendarConfigStore:
    """Adapter used by LabCalendarService without sharing IDs across profiles."""

    def __init__(self, profiles: CalendarProfileStore, profile_name: str) -> None:
        self.profiles = profiles
        self.profile_name = profile_name

    def load(self) -> CalendarLocalConfig:
        profile = self.profiles.load(self.profile_name)
        return CalendarLocalConfig(
            lab_calendar_id=profile.calendar_id,
            lab_calendar_name=profile.calendar_name,
        )

    def save(self, config: CalendarLocalConfig) -> None:
        profile = self.profiles.load(self.profile_name)
        profile.calendar_id = config.lab_calendar_id
        profile.calendar_name = config.lab_calendar_name or profile.calendar_name
        self.profiles.save(profile)


def credentials_source_from_environment() -> Path:
    return Path(os.getenv("GOOGLE_CALENDAR_CREDENTIALS_FILE", "credentials.json"))
