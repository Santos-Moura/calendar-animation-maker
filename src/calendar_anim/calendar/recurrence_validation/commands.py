import hashlib
import json
import re
from datetime import date
from pathlib import Path
from typing import Annotated, Never

import typer
from googleapiclient.errors import HttpError

from calendar_anim.browser.playwright_gateway import PlaywrightCalendarCaptureGateway
from calendar_anim.calendar.capture.models import (
    BrowserChannel,
    CalendarCaptureConfig,
)
from calendar_anim.calendar.lab import LabCalendarService
from calendar_anim.calendar.models import CalendarInfo
from calendar_anim.calendar.multi_frame.artifacts import AnimationRunStore
from calendar_anim.calendar.profiles.models import CalendarAccountProfile
from calendar_anim.calendar.profiles.service import CalendarProfileService
from calendar_anim.calendar.profiles.store import (
    DEFAULT_PROFILE_NAME,
    CalendarProfileStore,
    ProfileCalendarConfigStore,
)
from calendar_anim.calendar.recurrence_validation.artifacts import (
    RecurrenceValidationStore,
    compose_comparison,
)
from calendar_anim.calendar.recurrence_validation.gateway import (
    GoogleRecurrenceValidationGateway,
)
from calendar_anim.calendar.recurrence_validation.planner import (
    build_recurrence_validation_plan,
)
from calendar_anim.calendar.recurrence_validation.service import (
    RecurrenceValidationService,
)
from calendar_anim.exceptions import CalendarAnimError

DEFAULT_VALIDATION_ID = "recurrence-rdate-smallest-real-01"
DEFAULT_SOURCE_RUN_ID = "cayde-final-126x72-3fps-36s-01"
DEFAULT_START_WEEK = "2029-12-02"


def _fail(error: Exception) -> Never:
    typer.secho(f"Error: {error}", fg=typer.colors.RED, err=True)
    raise typer.Exit(code=1)


def _validation_id(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", value):
        raise CalendarAnimError(f"Invalid validation-id: {value!r}")
    return value


def _profile_service() -> CalendarProfileService:
    return CalendarProfileService(
        CalendarProfileStore(), gateway_factory=GoogleRecurrenceValidationGateway
    )


def _selected_profile(requested: str | None, planned: str) -> str:
    selected = requested or planned
    if selected != planned:
        raise CalendarAnimError(
            f"Validation belongs to profile {planned!r}, not requested profile {selected!r}"
        )
    return selected


def _recurrence_capture_config(
    profile: CalendarAccountProfile,
    profile_directory: Path | None = None,
) -> CalendarCaptureConfig:
    return CalendarCaptureConfig(
        profile_directory=profile_directory or profile.browser_profile_directory,
        browser_channel=BrowserChannel.CHROME,
        browser_zoom_percent=profile.capture_zoom_percent,
        visible_start_hour=6,
        visible_end_hour=24,
    )


def prepare_recurrence_validation_command(
    validation_id: Annotated[str, typer.Option("--validation-id")] = DEFAULT_VALIDATION_ID,
    source_run_id: Annotated[str, typer.Option("--source-run-id")] = DEFAULT_SOURCE_RUN_ID,
    source_frame_index: Annotated[int, typer.Option("--source-frame-index", min=0)] = 23,
    source_event_index: Annotated[int, typer.Option("--source-event-index", min=0)] = 0,
    start_week_value: Annotated[str, typer.Option("--start-week")] = DEFAULT_START_WEEK,
    profile_name: Annotated[str, typer.Option("--profile")] = DEFAULT_PROFILE_NAME,
    calendar_name: Annotated[str | None, typer.Option("--calendar-name")] = None,
    animation_output_root: Annotated[Path, typer.Option("--animation-output-root")] = Path(
        "output/animation-runs"
    ),
    output_root: Annotated[Path, typer.Option("--output-root")] = Path(
        "output/recurrence-validation"
    ),
) -> None:
    """Create the local six-week validation plan; never contact Google."""
    try:
        resolved_id = _validation_id(validation_id)
        plan = build_recurrence_validation_plan(
            AnimationRunStore(animation_output_root),
            validation_id=resolved_id,
            source_run_id=source_run_id,
            source_frame_index=source_frame_index,
            source_event_index=source_event_index,
            start_week=date.fromisoformat(start_week_value),
            calendar_profile=profile_name,
            calendar_name=calendar_name,
        )
        store = RecurrenceValidationStore(output_root)
        plan_path = store.save_plan(plan)
    except (CalendarAnimError, HttpError, OSError, ValueError) as error:
        _fail(error)
    typer.echo(f"Validation: {plan.validation_id}")
    typer.echo(f"Profile: {plan.calendar_profile}")
    typer.echo(f"Calendar: {plan.calendar_name}")
    typer.echo(f"Weeks: {plan.first_week} through {plan.last_week}")
    typer.echo("Recurring: 1 parent, 3 displayed instances (DTSTART + 2 RDATE values)")
    typer.echo("Controls: 3 standalone events")
    typer.echo("Expected events.insert calls: 4")
    typer.echo(f"Plan: {plan_path}")
    typer.echo("Google Calendar calls: NO")


def upload_recurrence_validation_command(
    validation_id: Annotated[str, typer.Option("--validation-id")],
    profile_name: Annotated[str | None, typer.Option("--profile")] = None,
    output_root: Annotated[Path, typer.Option("--output-root")] = Path(
        "output/recurrence-validation"
    ),
    execute: Annotated[
        bool, typer.Option("--execute", help="Create the four validation resources.")
    ] = False,
) -> None:
    """Preflight six clean weeks, then create one parent and three controls."""
    try:
        resolved_id = _validation_id(validation_id)
        store = RecurrenceValidationStore(output_root)
        plan = store.load_plan(resolved_id)
        selected_profile = _selected_profile(profile_name, plan.calendar_profile)
    except (CalendarAnimError, HttpError, OSError, ValueError) as error:
        _fail(error)
    typer.echo(f"Validation: {plan.validation_id}")
    typer.echo(f"PROFILE: {selected_profile}")
    typer.echo(f"Weeks: {plan.first_week} through {plan.last_week}")
    typer.echo("Resources: 1 recurring parent + 3 standalone controls")
    typer.echo("Displayed instances: 3 recurring + 3 standalone")
    typer.echo("Fresh-run events.insert calls: 4")
    typer.echo("Preflight: abort if any unrelated event exists in the six weeks")
    typer.echo(f"Execution: {'REAL' if execute else 'DRY RUN'}")
    if not execute:
        typer.echo("No authentication or Calendar API call was performed.")
        return
    typer.echo("\nThis writes only the four metadata-scoped validation resources.")
    try:
        profiles = _profile_service()
        profile, base_gateway = profiles.gateway(selected_profile)
        if profile.calendar_name != plan.calendar_name:
            raise CalendarAnimError(
                f"Profile calendar is {profile.calendar_name!r}, but validation targets "
                f"{plan.calendar_name!r}"
            )
        gateway = base_gateway
        if not isinstance(gateway, GoogleRecurrenceValidationGateway):
            raise CalendarAnimError("Profile gateway does not support recurrence validation")
        lab = LabCalendarService(
            gateway,
            ProfileCalendarConfigStore(profiles.store, selected_profile),
        )

        def resolve_existing(name: str, _timezone: str) -> tuple[CalendarInfo, bool]:
            calendar = lab.find(name)
            if calendar is None:
                raise CalendarAnimError("Configured laboratory calendar was not found")
            if selected_profile != DEFAULT_PROFILE_NAME and calendar.access_role != "owner":
                raise CalendarAnimError(
                    "Secondary profile does not own the selected validation calendar"
                )
            return calendar, False

        calendar, _created = resolve_existing(plan.calendar_name, plan.timezone)
        typer.echo(f"PROFILE: {profile.profile_name}")
        typer.echo(f"ACCOUNT: {profile.authenticated_google_account}")
        typer.echo(f"CALENDAR: {calendar.name}")
        typer.echo(f"CALENDAR ID: {calendar.id}")
        typer.echo("EXECUTION: REAL")
        typer.confirm("Create only this profile-scoped four-resource validation?", abort=True)
        state = RecurrenceValidationService(gateway, store, resolve_existing).upload(plan)
    except (CalendarAnimError, HttpError, OSError, ValueError) as error:
        _fail(error)
    typer.echo(f"Status: {state.status.value}")
    typer.echo(f"events.insert calls: {state.events_insert_calls}")
    typer.echo(f"rateLimitExceeded: {state.rate_limit_exceeded_count}")
    typer.echo(f"quotaExceeded: {state.quota_exceeded_count}")
    typer.echo(f"Upload report: {store.upload_report_path(plan.validation_id)}")


def capture_recurrence_validation_command(
    validation_id: Annotated[str, typer.Option("--validation-id")],
    profile_name: Annotated[str | None, typer.Option("--profile")] = None,
    output_root: Annotated[Path, typer.Option("--output-root")] = Path(
        "output/recurrence-validation"
    ),
    profile_directory: Annotated[Path | None, typer.Option("--profile-directory")] = None,
    execute: Annotated[
        bool, typer.Option("--execute", help="Open Playwright and capture all six weeks.")
    ] = False,
) -> None:
    """Capture three recurring/control pairs and build a side-by-side sheet."""
    try:
        resolved_id = _validation_id(validation_id)
        store = RecurrenceValidationStore(output_root)
        plan = store.load_plan(resolved_id)
        selected_profile = _selected_profile(profile_name, plan.calendar_profile)
        calendar_profile = CalendarProfileStore().load(selected_profile)
        state = store.load_state(resolved_id)
    except (CalendarAnimError, HttpError, OSError, ValueError) as error:
        _fail(error)
    typer.echo(f"Validation: {plan.validation_id}")
    typer.echo(f"Profile: {selected_profile}")
    typer.echo(f"Browser zoom: {calendar_profile.capture_zoom_percent}%")
    typer.echo("Positioning: required Calendar vertical scroller, 06:00-00:00")
    typer.echo("No-scroll fallback: disabled")
    typer.echo("Screenshots: 6 (3 recurring/control pairs)")
    typer.echo(f"Execution: {'REAL BROWSER' if execute else 'DRY RUN'}")
    typer.echo(f"Output: {store.capture_directory(plan.validation_id)}")
    if not execute:
        typer.echo("No browser was opened and no Calendar API call was made.")
        return
    if state is None or state.status.value != "completed":
        _fail(CalendarAnimError("Validation upload must be completed before capture"))
    config = _recurrence_capture_config(calendar_profile, profile_directory)
    hashes: dict[str, str] = {}
    try:
        with PlaywrightCalendarCaptureGateway(config) as gateway:
            for pair_index in range(3):
                for variant in ("recurring", "standalone"):
                    week = next(
                        item
                        for item in plan.weeks
                        if item.pair_index == pair_index and item.variant == variant
                    )
                    output = store.screenshot_path(plan.validation_id, pair_index, variant)
                    gateway.open_week(week.week_start)
                    gateway.wait_until_ready(week.week_start, week.expected_events)
                    gateway.capture(output)
                    hashes[output.name] = hashlib.sha256(output.read_bytes()).hexdigest()
                    typer.echo(f"Captured: {variant} pair {pair_index + 1} ({week.week_start})")
        comparison = compose_comparison(plan, store)
        capture_report = {
            "validation_id": plan.validation_id,
            "calendar_profile": plan.calendar_profile,
            "screenshots": hashes,
            "comparison": str(comparison),
            "browser_zoom_percent": calendar_profile.capture_zoom_percent,
            "visible_window": "06:00-00:00",
            "positioning_mode": "required-vertical-scroller",
            "no_scroll_fallback": False,
            "calendar_writes": False,
        }
        report_path = store.capture_directory(plan.validation_id) / "capture-report.json"
        report_path.write_text(json.dumps(capture_report, indent=2) + "\n", encoding="utf-8")
    except (CalendarAnimError, OSError, RuntimeError, ValueError) as error:
        _fail(error)
    typer.echo(f"Comparison: {comparison}")
    typer.echo(f"Capture report: {report_path}")
    typer.echo("Calendar writes during capture: NO")


def cleanup_recurrence_validation_command(
    validation_id: Annotated[str, typer.Option("--validation-id")],
    profile_name: Annotated[str | None, typer.Option("--profile")] = None,
    output_root: Annotated[Path, typer.Option("--output-root")] = Path(
        "output/recurrence-validation"
    ),
    execute: Annotated[
        bool, typer.Option("--execute", help="Delete only this validation's resources.")
    ] = False,
) -> None:
    """Delete only resources matched by this validation's private metadata."""
    try:
        resolved_id = _validation_id(validation_id)
        store = RecurrenceValidationStore(output_root)
        plan = store.load_plan(resolved_id)
        selected_profile = _selected_profile(profile_name, plan.calendar_profile)
    except (CalendarAnimError, OSError, ValueError) as error:
        _fail(error)
    typer.echo(f"Validation: {plan.validation_id}")
    typer.echo(f"PROFILE: {selected_profile}")
    typer.echo(
        "Cleanup filter: generated_by=calendar-anim-recurrence-validation + "
        f"validation_id={plan.validation_id} + calendar_profile={selected_profile}"
    )
    typer.echo(f"Execution: {'REAL DELETE' if execute else 'DRY RUN'}")
    if not execute:
        typer.echo("No authentication, Calendar lookup, or deletion was performed.")
        return
    try:
        profiles = _profile_service()
        profile, base_gateway = profiles.gateway(selected_profile)
        gateway = base_gateway
        if not isinstance(gateway, GoogleRecurrenceValidationGateway):
            raise CalendarAnimError("Profile gateway does not support recurrence validation")
        calendar = LabCalendarService(
            gateway,
            ProfileCalendarConfigStore(profiles.store, selected_profile),
        ).find(plan.calendar_name)
        if calendar is None:
            raise CalendarAnimError("Configured laboratory calendar was not found")
        if selected_profile != DEFAULT_PROFILE_NAME and calendar.access_role != "owner":
            raise CalendarAnimError("Secondary profile does not own the cleanup calendar")
        if profile.calendar_id != calendar.id:
            raise CalendarAnimError("Profile calendar ID changed during cleanup preflight")
        typer.echo(f"ACCOUNT: {profile.authenticated_google_account}")
        typer.echo(f"CALENDAR: {calendar.name}")
        typer.echo(f"CALENDAR ID: {calendar.id}")
        typer.echo("EXECUTION: REAL DELETE")
        typer.confirm("Delete only this profile-scoped validation?", abort=True)
        result = RecurrenceValidationService(
            gateway,
            store,
            lambda _name, _timezone: (calendar, False),
        ).cleanup(plan, calendar)
    except (CalendarAnimError, HttpError, OSError, ValueError) as error:
        _fail(error)
    typer.echo(f"Matched resources: {len(result.matched_resource_ids)}")
    typer.echo(f"Deleted resources: {result.deleted_resources}")
    typer.echo(f"Failed deletions: {result.failed_deletions}")
    typer.echo(f"Cleanup report: {store.cleanup_report_path(plan.validation_id)}")


def register_recurrence_validation_commands(app: typer.Typer) -> None:
    app.command("prepare-recurrence-validation")(prepare_recurrence_validation_command)
    app.command("upload-recurrence-validation")(upload_recurrence_validation_command)
    app.command("capture-recurrence-validation")(capture_recurrence_validation_command)
    app.command("cleanup-recurrence-validation")(cleanup_recurrence_validation_command)
