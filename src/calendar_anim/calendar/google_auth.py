import os
from pathlib import Path
from typing import Any, Final, cast

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

from calendar_anim.exceptions import IntegrationNotConfiguredError

CALENDAR_SCOPES: Final[list[str]] = [
    "https://www.googleapis.com/auth/calendar.app.created",
    "https://www.googleapis.com/auth/calendar.calendarlist.readonly",
]


class GoogleOAuthConfig:
    def __init__(
        self, credentials_file: Path | None = None, token_file: Path | None = None
    ) -> None:
        self.credentials_file = credentials_file or Path(
            os.getenv("GOOGLE_CALENDAR_CREDENTIALS_FILE", "credentials.json")
        )
        self.token_file = token_file or Path(os.getenv("GOOGLE_CALENDAR_TOKEN_FILE", "token.json"))

    @property
    def credentials_available(self) -> bool:
        return self.credentials_file.is_file()

    @property
    def token_available(self) -> bool:
        return self.token_file.is_file()


class GoogleOAuthClient:
    def __init__(self, config: GoogleOAuthConfig | None = None) -> None:
        self.config = config or GoogleOAuthConfig()

    def build_service(self) -> Any:
        credentials: Credentials | None = None
        if self.config.token_available:
            credentials = Credentials.from_authorized_user_file(  # type: ignore[no-untyped-call]
                str(self.config.token_file), CALENDAR_SCOPES
            )
        if credentials and credentials.expired and credentials.refresh_token:
            credentials.refresh(Request())  # type: ignore[no-untyped-call]
        if not credentials or not credentials.valid:
            if not self.config.credentials_available:
                raise IntegrationNotConfiguredError(
                    "Google Calendar credentials were not found. Create Desktop OAuth credentials, "
                    "save them as credentials.json, or set GOOGLE_CALENDAR_CREDENTIALS_FILE."
                )
            flow = InstalledAppFlow.from_client_secrets_file(
                str(self.config.credentials_file), CALENDAR_SCOPES
            )
            credentials = cast(Credentials, flow.run_local_server(port=0))
        self.config.token_file.parent.mkdir(parents=True, exist_ok=True)
        serialized = credentials.to_json()  # type: ignore[no-untyped-call]
        self.config.token_file.write_text(serialized, encoding="utf-8")
        return build("calendar", "v3", credentials=credentials, cache_discovery=False)
