from collections.abc import Callable
from typing import Any

from calendar_anim.calendar.google_auth import GoogleOAuthClient
from calendar_anim.calendar.google_gateway import GoogleCalendarGateway
from calendar_anim.calendar.profiles.models import (
    CalendarAccountProfile,
    CalendarProfileInspection,
)
from calendar_anim.calendar.profiles.store import CalendarProfileStore
from calendar_anim.exceptions import CalendarAnimError

OAuthClientFactory = Callable[[Any], GoogleOAuthClient]
GatewayFactory = Callable[[Any], GoogleCalendarGateway]


class CalendarProfileService:
    def __init__(
        self,
        store: CalendarProfileStore,
        oauth_client_factory: OAuthClientFactory = GoogleOAuthClient,
        gateway_factory: GatewayFactory = GoogleCalendarGateway,
    ) -> None:
        self.store = store
        self.oauth_client_factory = oauth_client_factory
        self.gateway_factory = gateway_factory

    def initialize(
        self,
        profile_name: str,
        *,
        calendar_name: str | None = None,
        timezone: str = "America/Sao_Paulo",
    ) -> CalendarAccountProfile:
        return self.store.initialize(
            profile_name,
            calendar_name=calendar_name,
            timezone=timezone,
        )

    def authenticate(
        self, profile_name: str
    ) -> tuple[CalendarAccountProfile, GoogleCalendarGateway]:
        profile = self.store.load(profile_name)
        gateway = self._build_gateway(profile)
        primary = gateway.get_calendar("primary")
        if primary is None:
            raise CalendarAnimError("Authenticated account has no accessible primary calendar")
        if (
            profile.authenticated_google_account is not None
            and profile.authenticated_google_account != primary.id
        ):
            raise CalendarAnimError(
                f"Profile {profile_name} token belongs to {primary.id}, expected "
                f"{profile.authenticated_google_account}"
            )
        profile.authenticated_google_account = primary.id
        self.store.save(profile)
        return profile, gateway

    def gateway(self, profile_name: str) -> tuple[CalendarAccountProfile, GoogleCalendarGateway]:
        profile = self.store.load(profile_name)
        if not profile.token_file.is_file():
            raise CalendarAnimError(
                f"Profile {profile_name} is not authenticated; token missing: {profile.token_file}"
            )
        gateway = self._build_gateway(profile)
        primary = gateway.get_calendar("primary")
        if primary is None:
            raise CalendarAnimError("Authenticated account has no accessible primary calendar")
        if (
            profile.authenticated_google_account is not None
            and profile.authenticated_google_account != primary.id
        ):
            raise CalendarAnimError(
                f"Wrong Google account for profile {profile_name}: {primary.id}"
            )
        if profile.authenticated_google_account is None:
            profile.authenticated_google_account = primary.id
            self.store.save(profile)
        return profile, gateway

    def inspect_local(self, profile_name: str) -> CalendarProfileInspection:
        profile = self.store.load(profile_name)
        return CalendarProfileInspection(
            profile_name=profile.profile_name,
            credentials_file=profile.credentials_file,
            credentials_present=profile.credentials_file.is_file(),
            token_file=profile.token_file,
            token_present=profile.token_file.is_file(),
            authenticated_google_account=profile.authenticated_google_account,
            calendar_id=profile.calendar_id,
            calendar_name=profile.calendar_name,
            timezone=profile.timezone,
            browser_profile_directory=profile.browser_profile_directory,
            capture_zoom_percent=profile.capture_zoom_percent,
        )

    def inspect_remote(self, profile_name: str) -> CalendarProfileInspection:
        profile, gateway = self.gateway(profile_name)
        calendar = gateway.get_calendar(profile.calendar_id) if profile.calendar_id else None
        inspection = self.inspect_local(profile_name)
        inspection.calendar_exists = calendar is not None if profile.calendar_id else False
        if calendar is not None:
            inspection.calendar_name = calendar.name
            inspection.timezone = calendar.timezone
            inspection.calendar_access_role = calendar.access_role
        return inspection

    def _build_gateway(self, profile: CalendarAccountProfile) -> GoogleCalendarGateway:
        oauth = self.oauth_client_factory(self.store.oauth_config(profile))
        return self.gateway_factory(oauth.build_service())
