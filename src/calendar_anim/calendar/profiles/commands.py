from pathlib import Path
from typing import Annotated, Never

import typer
from googleapiclient.errors import HttpError

from calendar_anim.calendar.lab import LAB_CALENDAR_DESCRIPTION, LabCalendarService
from calendar_anim.calendar.profiles.models import CalendarProfileInspection
from calendar_anim.calendar.profiles.service import CalendarProfileService
from calendar_anim.calendar.profiles.store import (
    DEFAULT_SECONDARY_CALENDAR_NAME,
    CalendarProfileStore,
    ProfileCalendarConfigStore,
)
from calendar_anim.exceptions import CalendarAnimError


def _fail(error: Exception) -> Never:
    typer.secho(f"Error: {error}", fg=typer.colors.RED, err=True)
    raise typer.Exit(code=1)


def _service(root: Path) -> CalendarProfileService:
    return CalendarProfileService(CalendarProfileStore(root=root))


def _print_inspection(inspection: CalendarProfileInspection) -> None:
    typer.echo(f"PROFILE: {inspection.profile_name}")
    typer.echo(f"Authenticated Google account: {inspection.authenticated_google_account or 'NO'}")
    typer.echo(f"Credentials: {inspection.credentials_file}")
    typer.echo(f"Credentials present: {'YES' if inspection.credentials_present else 'NO'}")
    typer.echo(f"Token: {inspection.token_file}")
    typer.echo(f"Token present: {'YES' if inspection.token_present else 'NO'}")
    typer.echo(f"CALENDAR: {inspection.calendar_name}")
    typer.echo(f"CALENDAR ID: {inspection.calendar_id or 'not selected'}")
    typer.echo(f"Timezone: {inspection.timezone}")
    typer.echo(f"Browser profile: {inspection.browser_profile_directory}")
    typer.echo(f"Capture zoom: {inspection.capture_zoom_percent}%")
    if inspection.calendar_exists is not None:
        typer.echo(f"Calendar exists remotely: {'YES' if inspection.calendar_exists else 'NO'}")
    if inspection.calendar_access_role is not None:
        typer.echo(f"Calendar access role: {inspection.calendar_access_role}")


def profiles_initialize_command(
    profile_name: Annotated[str, typer.Option("--profile")],
    calendar_name: Annotated[str, typer.Option("--calendar-name")] = (
        DEFAULT_SECONDARY_CALENDAR_NAME
    ),
    timezone: Annotated[str, typer.Option("--timezone")] = "America/Sao_Paulo",
    profiles_root: Annotated[Path, typer.Option("--profiles-root")] = Path(
        ".calendar-anim/profiles"
    ),
) -> None:
    """Initialize only local profile metadata; no OAuth or Google request."""
    try:
        service = _service(profiles_root)
        profile = service.initialize(
            profile_name,
            calendar_name=calendar_name,
            timezone=timezone,
        )
        inspection = service.inspect_local(profile.profile_name)
    except (CalendarAnimError, OSError, ValueError) as error:
        _fail(error)
    _print_inspection(inspection)
    typer.echo("Google calls: NO")


def profiles_list_command(
    profiles_root: Annotated[Path, typer.Option("--profiles-root")] = Path(
        ".calendar-anim/profiles"
    ),
) -> None:
    """List local profiles without opening OAuth or contacting Google."""
    try:
        profiles = CalendarProfileStore(root=profiles_root).list_profiles()
    except (CalendarAnimError, OSError, ValueError) as error:
        _fail(error)
    for profile in profiles:
        typer.echo(
            f"{profile.profile_name}: calendar={profile.calendar_name!r}, "
            f"calendar_id={profile.calendar_id or 'none'}, "
            f"token={'present' if profile.token_file.is_file() else 'missing'}"
        )
    typer.echo("Google calls: NO")


def profiles_auth_command(
    profile_name: Annotated[str, typer.Option("--profile")],
    calendar_name: Annotated[str, typer.Option("--calendar-name")] = (
        DEFAULT_SECONDARY_CALENDAR_NAME
    ),
    profiles_root: Annotated[Path, typer.Option("--profiles-root")] = Path(
        ".calendar-anim/profiles"
    ),
    execute: Annotated[
        bool, typer.Option("--execute", help="Open OAuth and save this profile's token.")
    ] = False,
) -> None:
    """Authenticate one Google account into its isolated token path."""
    try:
        service = _service(profiles_root)
        profile = service.initialize(profile_name, calendar_name=calendar_name)
        inspection = service.inspect_local(profile.profile_name)
    except (CalendarAnimError, OSError, ValueError) as error:
        _fail(error)
    _print_inspection(inspection)
    typer.echo(f"EXECUTION: {'OAUTH' if execute else 'DRY RUN'}")
    if not execute:
        typer.echo("No browser was opened and no token was created or changed.")
        return
    typer.echo("Log in through Google's browser page; no password is read by this program.")
    typer.confirm(
        f"Authenticate profile {profile.profile_name} and write only {profile.token_file}?",
        default=False,
        abort=True,
    )
    try:
        profile, _gateway = service.authenticate(profile.profile_name)
        inspection = service.inspect_local(profile.profile_name)
    except (CalendarAnimError, HttpError, OSError, ValueError) as error:
        _fail(error)
    typer.echo("Authentication completed.")
    _print_inspection(inspection)


def profiles_inspect_command(
    profile_name: Annotated[str, typer.Option("--profile")],
    profiles_root: Annotated[Path, typer.Option("--profiles-root")] = Path(
        ".calendar-anim/profiles"
    ),
    remote: Annotated[
        bool, typer.Option("--remote", help="Perform read-only account/calendar verification.")
    ] = False,
) -> None:
    """Inspect profile paths and identities without exposing token contents."""
    try:
        service = _service(profiles_root)
        inspection = (
            service.inspect_remote(profile_name) if remote else service.inspect_local(profile_name)
        )
    except (CalendarAnimError, HttpError, OSError, ValueError) as error:
        _fail(error)
    _print_inspection(inspection)
    typer.echo(f"Remote reads: {'YES' if remote else 'NO'}")
    typer.echo("Google Calendar writes: NO")


def profiles_create_calendar_command(
    profile_name: Annotated[str, typer.Option("--profile")],
    name: Annotated[str, typer.Option("--name")],
    timezone: Annotated[str, typer.Option("--timezone")] = "America/Sao_Paulo",
    profiles_root: Annotated[Path, typer.Option("--profiles-root")] = Path(
        ".calendar-anim/profiles"
    ),
    execute: Annotated[
        bool, typer.Option("--execute", help="Select or create the secondary calendar.")
    ] = False,
) -> None:
    """Select an existing marked lab calendar or explicitly create it."""
    try:
        service = _service(profiles_root)
        profile = service.initialize(profile_name, calendar_name=name, timezone=timezone)
        inspection = service.inspect_local(profile.profile_name)
    except (CalendarAnimError, OSError, ValueError) as error:
        _fail(error)
    _print_inspection(inspection)
    typer.echo(f"Description marker: {LAB_CALENDAR_DESCRIPTION}")
    typer.echo(f"EXECUTION: {'REAL' if execute else 'DRY RUN'}")
    if not execute:
        typer.echo("No authentication, lookup, calendar selection, or creation was performed.")
        return
    try:
        profile, gateway = service.gateway(profile.profile_name)
        lab = LabCalendarService(
            gateway,
            ProfileCalendarConfigStore(service.store, profile.profile_name),
        )
        existing = lab.find(name)
        if existing is not None and existing.access_role != "owner":
            raise CalendarAnimError(
                "Refusing to select a secondary calendar not owned by this profile's account"
            )
    except (CalendarAnimError, HttpError, OSError, ValueError) as error:
        _fail(error)
    typer.echo(f"PROFILE: {profile.profile_name}")
    typer.echo(f"ACCOUNT: {profile.authenticated_google_account}")
    typer.echo(f"CALENDAR: {name}")
    typer.echo(f"CALENDAR ID: {existing.id if existing else 'will be created'}")
    typer.echo("EXECUTION: REAL")
    if existing is None:
        typer.confirm("Create this dedicated non-primary calendar?", default=False, abort=True)
    else:
        typer.confirm("Select this existing dedicated calendar?", default=False, abort=True)
    try:
        calendar, created = lab.resolve(name, timezone)
    except (CalendarAnimError, HttpError, OSError, ValueError) as error:
        _fail(error)
    typer.echo(f"Calendar: {calendar.name}")
    typer.echo(f"Calendar ID: {calendar.id}")
    typer.echo(f"Created: {'YES' if created else 'NO (selected existing)'}")


def register_profile_commands(app: typer.Typer) -> None:
    profiles = typer.Typer(help="Manage isolated Google Calendar account profiles.")
    profiles.command("init")(profiles_initialize_command)
    profiles.command("list")(profiles_list_command)
    profiles.command("auth")(profiles_auth_command)
    profiles.command("inspect")(profiles_inspect_command)
    profiles.command("create-calendar")(profiles_create_calendar_command)
    app.add_typer(profiles, name="profiles")
