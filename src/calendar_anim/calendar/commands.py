import logging
import re
from datetime import date
from pathlib import Path
from typing import Annotated, Never

import typer
from googleapiclient.errors import HttpError

from calendar_anim.calendar.calibration.artifacts import (
    write_dry_run_artifacts,
    write_execution_result,
    write_observations,
    write_report,
)
from calendar_anim.calendar.calibration.models import (
    CalibrationExecutionResult,
    CalibrationObservations,
    CalibrationPattern,
)
from calendar_anim.calendar.calibration.patterns import (
    DEFAULT_CALENDAR_NAME,
    DEFAULT_MAX_EVENTS,
    PATTERNS,
    build_calibration_plan,
)
from calendar_anim.calendar.calibration.profile import (
    DEFAULT_PROFILE_PATH,
    apply_observations,
    load_observations,
    load_profile,
    profile_summary,
    save_profile,
)
from calendar_anim.calendar.calibration.service import CalibrationService, CleanupMatch
from calendar_anim.calendar.gateway import CalendarGateway
from calendar_anim.calendar.google_auth import GoogleOAuthClient, GoogleOAuthConfig
from calendar_anim.calendar.google_gateway import GoogleCalendarGateway
from calendar_anim.calendar.lab import LAB_CALENDAR_DESCRIPTION, LabCalendarService
from calendar_anim.calendar.local_config import CalendarConfigStore
from calendar_anim.exceptions import CalendarAnimError


def _fail(error: Exception) -> Never:
    typer.secho(f"Error: {error}", fg=typer.colors.RED, err=True)
    raise typer.Exit(code=1)


def _configure_logging(verbose: bool, debug: bool) -> None:
    level = logging.DEBUG if debug else logging.INFO if verbose else logging.WARNING
    logging.getLogger().setLevel(level)


def _google_gateway() -> GoogleCalendarGateway:
    return GoogleCalendarGateway(GoogleOAuthClient().build_service())


def _valid_identifier(value: str, label: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", value):
        raise CalendarAnimError(f"Invalid {label}: {value!r}")
    return value


def calibration_patterns_command() -> None:
    """List deterministic visual calibration experiments."""
    for pattern in PATTERNS.values():
        typer.echo(
            f"{pattern.name:<18} {pattern.description} (~{pattern.approximate_events} events)"
        )


def calibrate_command(
    pattern_value: Annotated[str, typer.Option("--pattern", help="Calibration pattern name.")],
    start_date_value: Annotated[
        str, typer.Option("--start-date", help="First calibration day (YYYY-MM-DD).")
    ],
    timezone: Annotated[str, typer.Option("--timezone")] = "America/Sao_Paulo",
    calendar_name: Annotated[str, typer.Option("--calendar-name")] = DEFAULT_CALENDAR_NAME,
    max_events: Annotated[int, typer.Option("--max-events")] = DEFAULT_MAX_EVENTS,
    output: Annotated[Path | None, typer.Option("--output", "-o")] = None,
    run_id: Annotated[str | None, typer.Option("--run-id")] = None,
    execute: Annotated[
        bool, typer.Option("--execute", help="Create real Calendar events.")
    ] = False,
    yes: Annotated[bool, typer.Option("--yes", help="Skip confirmation with --execute.")] = False,
    verbose: Annotated[bool, typer.Option("--verbose", "-v")] = False,
    debug: Annotated[bool, typer.Option("--debug")] = False,
) -> None:
    """Build calibration artifacts; real writes require --execute."""
    _configure_logging(verbose, debug)
    try:
        if pattern_value not in PATTERNS:
            raise CalendarAnimError(
                f"Unknown calibration pattern: {pattern_value}. "
                f"Choose one of: {', '.join(PATTERNS)}"
            )
        pattern: CalibrationPattern = pattern_value
        start_date = date.fromisoformat(start_date_value)
        plan = build_calibration_plan(
            pattern=pattern,
            start_date=start_date,
            timezone=timezone,
            calendar_name=calendar_name,
            max_events=max_events,
            run_id=run_id,
        )
        output_dir = output or Path("output/calibration") / plan.run_id
        write_dry_run_artifacts(plan, output_dir)
    except (CalendarAnimError, OSError, ValueError) as error:
        _fail(error)
    typer.echo(f"Calendar: {plan.calendar_name}")
    typer.echo(f"Pattern: {plan.pattern}")
    typer.echo(f"Animation ID: {plan.animation_id}")
    typer.echo(f"Run ID: {plan.run_id}")
    typer.echo(f"Events: {plan.event_count} / {plan.max_events}")
    typer.echo(f"Start date: {plan.start_date}")
    typer.echo(f"Timezone: {plan.timezone}")
    typer.echo(f"Execution: {'REAL' if execute else 'DRY RUN'}")
    typer.echo(f"Artifacts: {output_dir}")
    if not execute:
        if yes:
            typer.echo("--yes has no effect without --execute; no API call was made.")
        return
    if not yes:
        typer.echo("\nThis will create real events in a dedicated Google Calendar.")
        typer.confirm("Continue?", default=False, abort=True)
    try:
        gateway = _google_gateway()
        service = CalibrationService(gateway, LabCalendarService(gateway, CalendarConfigStore()))
        result = service.execute(plan)
        write_execution_result(result, output_dir)
        write_report(plan, output_dir, executed=True)
    except (CalendarAnimError, HttpError, OSError) as error:
        _fail(error)
    typer.echo(f"Calendar ID: {result.calendar_id}")
    typer.echo(f"Created events: {result.created_events}")
    typer.echo(f"Failed events: {result.failed_events}")
    if result.errors:
        for result_error in result.errors:
            typer.secho(f"Error: {result_error}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)


def _local_cleanup_result(run_id: str) -> CalibrationExecutionResult | None:
    path = Path("output/calibration") / run_id / "execution-result.json"
    if not path.is_file():
        return None
    try:
        return CalibrationExecutionResult.model_validate_json(path.read_text(encoding="utf-8"))
    except ValueError:
        return None


def _find_cleanup_match(
    gateway: CalendarGateway,
    calendar_name: str,
    calendar_id: str | None,
    animation_id: str,
    run_id: str,
) -> tuple[CleanupMatch, CalibrationService]:
    lab = LabCalendarService(gateway, CalendarConfigStore())
    service = CalibrationService(gateway, lab)
    if not calendar_id:
        return service.find_cleanup_matches(calendar_name, animation_id, run_id), service
    calendar = gateway.get_calendar(calendar_id)
    if not calendar:
        raise CalendarAnimError(f"Calendar not found: {calendar_id}")
    if calendar.primary or calendar.description != LAB_CALENDAR_DESCRIPTION:
        raise CalendarAnimError("Refusing cleanup outside the recognized laboratory calendar")
    metadata = {
        "generated_by": "calendar-anim",
        "animation_id": animation_id,
        "run_id": run_id,
    }
    events = gateway.find_events_by_private_metadata(calendar.id, metadata)
    return CleanupMatch(calendar=calendar, events=events), service


def cleanup_command(
    animation_id: Annotated[str, typer.Option("--animation-id")],
    run_id: Annotated[str, typer.Option("--run-id")],
    calendar_name: Annotated[str, typer.Option("--calendar-name")] = DEFAULT_CALENDAR_NAME,
    calendar_id: Annotated[str | None, typer.Option("--calendar-id")] = None,
    execute: Annotated[
        bool, typer.Option("--execute", help="Delete matching real events.")
    ] = False,
    yes: Annotated[bool, typer.Option("--yes", help="Skip confirmation with --execute.")] = False,
) -> None:
    """Delete only events matching generated_by, animation_id, and run_id."""
    try:
        animation_id = _valid_identifier(animation_id, "animation-id")
        run_id = _valid_identifier(run_id, "run-id")
    except CalendarAnimError as error:
        _fail(error)
    if not execute:
        auth = GoogleOAuthConfig()
        if auth.token_available:
            try:
                match, _ = _find_cleanup_match(
                    _google_gateway(), calendar_name, calendar_id, animation_id, run_id
                )
                count = len(match.events)
                source = "authenticated metadata lookup"
                display_calendar = match.calendar.name if match.calendar else calendar_name
            except (CalendarAnimError, HttpError, OSError) as error:
                _fail(error)
        else:
            local = _local_cleanup_result(run_id)
            count = (
                local.created_events
                if local and local.executed and local.animation_id == animation_id
                else 0
            )
            source = "local execution record; authentication was not configured"
            display_calendar = calendar_name
        typer.echo(f"Calendar: {display_calendar}")
        typer.echo(f"Animation ID: {animation_id}")
        typer.echo(f"Run ID: {run_id}")
        typer.echo(f"Matching events: {count} ({source})")
        typer.echo("Execution: DRY RUN")
        typer.echo("No deletion was performed.")
        if yes:
            typer.echo("--yes has no effect without --execute.")
        return
    try:
        gateway = _google_gateway()
        match, service = _find_cleanup_match(
            gateway, calendar_name, calendar_id, animation_id, run_id
        )
        typer.echo(f"Calendar: {match.calendar.name if match.calendar else calendar_name}")
        typer.echo(f"Animation ID: {animation_id}")
        typer.echo(f"Run ID: {run_id}")
        typer.echo(f"Matching events: {len(match.events)}")
        typer.echo("Execution: REAL")
        if not match.events:
            typer.echo("No matching events; nothing was changed.")
            return
        if not yes:
            typer.confirm("Delete only these matching events?", default=False, abort=True)
        result = service.cleanup(match)
    except (CalendarAnimError, HttpError, OSError) as error:
        _fail(error)
    typer.echo(f"Deleted events: {result.deleted_events}")
    typer.echo(f"Failed deletions: {result.failed_events}")
    if result.errors:
        raise typer.Exit(code=1)


def lab_info_command(
    calendar_name: Annotated[str, typer.Option("--calendar-name")] = DEFAULT_CALENDAR_NAME,
) -> None:
    """Show local authentication and laboratory-calendar state."""
    auth = GoogleOAuthConfig()
    local = CalendarConfigStore().load()
    typer.echo(f"Calendar name: {calendar_name}")
    typer.echo(f"Configured ID: {local.lab_calendar_id or 'none'}")
    typer.echo(f"Credentials file: {'present' if auth.credentials_available else 'missing'}")
    typer.echo(f"Authentication token: {'present' if auth.token_available else 'missing'}")
    if not auth.token_available:
        typer.echo("Remote lookup skipped because no authentication token is configured.")
        return
    try:
        gateway = _google_gateway()
        calendar = LabCalendarService(gateway, CalendarConfigStore()).find(calendar_name)
    except (CalendarAnimError, HttpError, OSError) as error:
        _fail(error)
    typer.echo(f"Exists in account: {'yes' if calendar else 'no'}")
    if calendar:
        typer.echo(f"Timezone: {calendar.timezone}")


def record_calibration_command(
    run_id: Annotated[str, typer.Option("--run-id")],
    pattern: Annotated[str | None, typer.Option("--pattern")] = None,
    minimum_event_minutes: Annotated[int | None, typer.Option("--minimum-event-minutes")] = None,
    minimum_visible_event_minutes: Annotated[
        int | None, typer.Option("--minimum-visible-event-minutes")
    ] = None,
    minimum_distinguishable_height_minutes: Annotated[
        int | None, typer.Option("--minimum-distinguishable-height-minutes")
    ] = None,
    usable_overlap_columns: Annotated[int | None, typer.Option("--usable-overlap-columns")] = None,
    maximum_tested_overlap_columns: Annotated[
        int | None, typer.Option("--maximum-tested-overlap-columns")
    ] = None,
    browser_zoom: Annotated[int | None, typer.Option("--browser-zoom")] = None,
    viewport_width: Annotated[int | None, typer.Option("--viewport-width")] = None,
    viewport_height: Annotated[int | None, typer.Option("--viewport-height")] = None,
    timezone: Annotated[str, typer.Option("--timezone")] = "America/Sao_Paulo",
    visible_start_hour: Annotated[int, typer.Option("--visible-start-hour")] = 6,
    visible_end_hour: Annotated[int, typer.Option("--visible-end-hour")] = 18,
    sidebar_visible: Annotated[bool, typer.Option("--sidebar-visible/--sidebar-hidden")] = False,
    weekends_visible: Annotated[bool, typer.Option("--weekends-visible/--weekends-hidden")] = True,
    titles_visible: Annotated[
        bool | None, typer.Option("--titles-visible/--titles-not-visible")
    ] = None,
    colors_distinguishable: Annotated[
        bool | None,
        typer.Option("--colors-distinguishable/--colors-not-distinguishable"),
    ] = None,
    notes: Annotated[str, typer.Option("--notes")] = "",
    output: Annotated[Path | None, typer.Option("--output", "-o")] = None,
    profile_output: Annotated[Path | None, typer.Option("--profile-output")] = None,
) -> None:
    """Record manual observations from the Google Calendar UI."""
    try:
        run_id = _valid_identifier(run_id, "run-id")
        if (
            minimum_event_minutes is not None
            and minimum_visible_event_minutes is not None
            and minimum_event_minutes != minimum_visible_event_minutes
        ):
            raise CalendarAnimError(
                "--minimum-event-minutes and --minimum-visible-event-minutes conflict"
            )
        effective_minimum_visible = (
            minimum_visible_event_minutes
            if minimum_visible_event_minutes is not None
            else minimum_event_minutes
        )
        observations = CalibrationObservations(
            run_id=run_id,
            pattern=pattern,
            calendar_ui={
                "view": "week",
                "timezone": timezone,
                "browser_zoom_percent": browser_zoom,
                "viewport_width": viewport_width,
                "viewport_height": viewport_height,
                "weekends_visible": weekends_visible,
                "sidebar_visible": sidebar_visible,
                "visible_start_hour": visible_start_hour,
                "visible_end_hour": visible_end_hour,
            },
            observations={
                "minimum_visible_event_minutes": effective_minimum_visible,
                "minimum_distinguishable_height_minutes": (minimum_distinguishable_height_minutes),
                "minimum_event_minutes": minimum_event_minutes,
                "usable_overlap_columns": usable_overlap_columns,
                "maximum_tested_overlap_columns": maximum_tested_overlap_columns,
                "titles_visible": titles_visible,
                "colors_distinguishable": colors_distinguishable,
                "notes": notes,
            },
        )
        path = output or Path("output/calibration") / run_id / "calibration-observations.yaml"
        write_observations(observations, path)
        resolved_profile_path = profile_output or (
            DEFAULT_PROFILE_PATH if output is None else path.with_name("calibration-profile.yaml")
        )
        profile = apply_observations(load_profile(resolved_profile_path), observations)
        save_profile(profile, resolved_profile_path)
    except (CalendarAnimError, OSError, ValueError) as error:
        _fail(error)
    typer.echo(f"Observations: {path}")
    typer.echo(f"Calibration profile: {resolved_profile_path}")


def calibration_summary_command(
    run_id: Annotated[str | None, typer.Option("--run-id")] = None,
    profile_path: Annotated[Path, typer.Option("--profile")] = DEFAULT_PROFILE_PATH,
) -> None:
    """Print the locally recorded Calendar UI-to-pixel mapping; never calls an API."""
    try:
        profile = load_profile(profile_path)
        source = str(profile_path)
        if run_id is not None:
            run_id = _valid_identifier(run_id, "run-id")
            observation_path = Path("output/calibration") / run_id / "calibration-observations.yaml"
            if not observation_path.is_file():
                raise CalendarAnimError(f"Calibration observations not found for run ID: {run_id}")
            profile = apply_observations(profile, load_observations(observation_path))
            source = f"{source} + {observation_path}"
    except (CalendarAnimError, OSError, ValueError) as error:
        _fail(error)
    typer.echo(profile_summary(profile))
    typer.echo(f"\nSource: {source}")
    typer.echo("This command used local files only; no Calendar API call was made.")


def register_calendar_commands(app: typer.Typer) -> None:
    app.command("calibration-patterns")(calibration_patterns_command)
    app.command("calibrate")(calibrate_command)
    app.command("cleanup")(cleanup_command)
    app.command("lab-info")(lab_info_command)
    app.command("record-calibration")(record_calibration_command)
    app.command("calibration-summary")(calibration_summary_command)
