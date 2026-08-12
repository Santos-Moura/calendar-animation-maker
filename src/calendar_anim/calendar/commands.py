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
    EVENT_COLORS,
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
from calendar_anim.calendar.capture.commands import register_capture_commands
from calendar_anim.calendar.frame_mapping.artifacts import (
    write_frame_execution_result,
    write_frame_mapping_artifacts,
)
from calendar_anim.calendar.frame_mapping.mapper import (
    build_single_frame_plan,
    select_frame,
    synchronized_horizontal_bands_ready,
)
from calendar_anim.calendar.frame_mapping.models import (
    DEFAULT_EVENT_COMPRESSION,
    EventCompressionMode,
    FitMode,
    FrameMappingMode,
    SingleFrameExecutionResult,
)
from calendar_anim.calendar.frame_mapping.service import (
    ABSOLUTE_SINGLE_FRAME_MAX_EVENTS,
    DEFAULT_SINGLE_FRAME_MAX_EVENTS,
    SingleFrameMappingService,
)
from calendar_anim.calendar.gateway import CalendarGateway
from calendar_anim.calendar.google_auth import GoogleOAuthClient, GoogleOAuthConfig
from calendar_anim.calendar.google_gateway import GoogleCalendarGateway
from calendar_anim.calendar.horizontal_band_compression.commands import (
    register_horizontal_band_compression_commands,
)
from calendar_anim.calendar.lab import LAB_CALENDAR_DESCRIPTION, LabCalendarService
from calendar_anim.calendar.local_config import CalendarConfigStore
from calendar_anim.calendar.multi_frame.commands import register_multi_frame_commands
from calendar_anim.calendar.recurrence_compaction.commands import (
    register_recurrence_compaction_commands,
)
from calendar_anim.calendar.subcolumn_ordering import (
    SubcolumnOrderStrategy,
    format_summary_key,
)
from calendar_anim.calendar.vertical_compression.commands import (
    register_vertical_compression_commands,
)
from calendar_anim.exceptions import CalendarAnimError
from calendar_anim.renderer.manifest import read_manifest, validate_manifest_files


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


def _csv_values(value: str | None, label: str) -> list[str] | None:
    if value is None:
        return None
    values = [item.strip() for item in value.split(",") if item.strip()]
    if len(values) != len(set(values)):
        raise CalendarAnimError(f"Duplicate values in {label}")
    return values


def _color_ids(value: str | None, label: str) -> list[str] | None:
    values = _csv_values(value, label)
    if values is None:
        return None
    supported = {color_id for color_id, _ in EVENT_COLORS}
    unknown = [color_id for color_id in values if color_id not in supported]
    if unknown:
        raise CalendarAnimError(f"Unsupported color IDs in {label}: {', '.join(unknown)}")
    return values


def _similar_color_groups(value: str | None) -> list[list[str]] | None:
    if value is None:
        return None
    groups = [group.strip() for group in value.split(";") if group.strip()]
    parsed = [_color_ids(group, "--similar-color-groups") or [] for group in groups]
    if any(len(group) < 2 for group in parsed):
        raise CalendarAnimError("Each similar color group must contain at least two IDs")
    return parsed


def _slot_order(value: str | None, label: str) -> list[int] | None:
    values = _csv_values(value, label)
    if values is None:
        return None
    try:
        order = [int(item) for item in values]
    except ValueError as error:
        raise CalendarAnimError(f"{label} must contain integer slot indexes") from error
    if sorted(order) != list(range(6)):
        raise CalendarAnimError(f"{label} must contain each slot index from 0 to 5 exactly once")
    return order


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


def _local_cleanup_result(
    run_id: str,
) -> CalibrationExecutionResult | SingleFrameExecutionResult | None:
    candidates: tuple[
        tuple[Path, type[CalibrationExecutionResult | SingleFrameExecutionResult]], ...
    ] = (
        (
            Path("output/calibration") / run_id / "execution-result.json",
            CalibrationExecutionResult,
        ),
        (
            Path("output/frame-mapping") / run_id / "execution-result.json",
            SingleFrameExecutionResult,
        ),
    )
    for path, result_model in candidates:
        if not path.is_file():
            continue
        try:
            return result_model.model_validate_json(path.read_text(encoding="utf-8"))
        except ValueError:
            continue
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
    preferred_color_ids: Annotated[
        str | None, typer.Option("--preferred-color-ids", help="Comma-separated color IDs.")
    ] = None,
    recommended_color_count: Annotated[
        int | None, typer.Option("--recommended-color-count")
    ] = None,
    poor_contrast_color_ids: Annotated[
        str | None, typer.Option("--poor-contrast-color-ids", help="Comma-separated color IDs.")
    ] = None,
    similar_color_groups: Annotated[
        str | None,
        typer.Option(
            "--similar-color-groups",
            help="Semicolon-separated groups of comma-separated IDs, e.g. 2,10;4,6.",
        ),
    ] = None,
    week_alignment_ok: Annotated[
        bool | None, typer.Option("--week-alignment-ok/--week-alignment-not-ok")
    ] = None,
    timezone_alignment_ok: Annotated[
        bool | None, typer.Option("--timezone-alignment-ok/--timezone-alignment-not-ok")
    ] = None,
    day_alignment_ok: Annotated[
        bool | None, typer.Option("--day-alignment-ok/--day-alignment-not-ok")
    ] = None,
    vertical_alignment_ok: Annotated[
        bool | None, typer.Option("--vertical-alignment-ok/--vertical-alignment-not-ok")
    ] = None,
    week_starts_on: Annotated[str | None, typer.Option("--week-starts-on")] = None,
    independent_cells_appear_contiguous: Annotated[
        bool | None,
        typer.Option("--independent-cells-contiguous/--independent-cells-not-contiguous"),
    ] = None,
    visible_gaps_between_cells: Annotated[
        bool | None, typer.Option("--visible-cell-gaps/--no-visible-cell-gaps")
    ] = None,
    same_color_cells_merge_visually: Annotated[
        bool | None,
        typer.Option("--same-color-cells-merge/--same-color-cells-do-not-merge"),
    ] = None,
    maximum_useful_bar_width: Annotated[
        int | None, typer.Option("--maximum-useful-bar-width")
    ] = None,
    partial_bar_positioning_predictable: Annotated[
        bool | None,
        typer.Option(
            "--partial-bar-positioning-predictable/--partial-bar-positioning-unpredictable"
        ),
    ] = None,
    recommended_horizontal_strategy: Annotated[
        str | None, typer.Option("--recommended-horizontal-strategy")
    ] = None,
    visual_order_forward: Annotated[
        str | None,
        typer.Option("--visual-order-forward", help="Observed S0..S5 order, comma-separated."),
    ] = None,
    visual_order_reverse: Annotated[
        str | None,
        typer.Option("--visual-order-reverse", help="Observed S0..S5 order, comma-separated."),
    ] = None,
    visual_order_shuffled: Annotated[
        str | None,
        typer.Option("--visual-order-shuffled", help="Observed S0..S5 order, comma-separated."),
    ] = None,
    stable_after_refresh: Annotated[
        bool | None,
        typer.Option("--stable-after-refresh/--not-stable-after-refresh"),
    ] = None,
    stable_after_navigation: Annotated[
        bool | None,
        typer.Option("--stable-after-navigation/--not-stable-after-navigation"),
    ] = None,
    stable_after_reopen: Annotated[
        bool | None,
        typer.Option("--stable-after-reopen/--not-stable-after-reopen"),
    ] = None,
    creation_order_controls_layout: Annotated[
        bool | None,
        typer.Option("--creation-order-controls-layout/--creation-order-does-not-control-layout"),
    ] = None,
    recommended_slot_order_strategy: Annotated[
        str | None,
        typer.Option(
            "--recommended-slot-order-strategy",
            help="One of: creation-order, summary-prefix, stable-alternative, unusable, none.",
        ),
    ] = None,
    ordering_factor_tested: Annotated[
        bool | None,
        typer.Option("--ordering-factor-tested/--ordering-factor-not-tested"),
    ] = None,
    ordering_controlling_property: Annotated[
        str | None,
        typer.Option(
            "--ordering-controlling-property",
            help="Observed ordering property: summary, color_id, or unknown.",
        ),
    ] = None,
    ordering_factor_stable: Annotated[
        bool | None,
        typer.Option("--ordering-factor-stable/--ordering-factor-unstable"),
    ] = None,
    notes: Annotated[str, typer.Option("--notes")] = "",
    observations_file: Annotated[
        Path | None,
        typer.Option(
            "--observations-file",
            help="Import a manually completed calibration-observations.yaml file.",
        ),
    ] = None,
    output: Annotated[Path | None, typer.Option("--output", "-o")] = None,
    profile_output: Annotated[Path | None, typer.Option("--profile-output")] = None,
) -> None:
    """Record manual observations from the Google Calendar UI."""
    try:
        run_id = _valid_identifier(run_id, "run-id")
        if observations_file is not None:
            observations = load_observations(observations_file)
            if observations.run_id != run_id:
                raise CalendarAnimError(
                    f"Observation run ID {observations.run_id!r} does not match --run-id {run_id!r}"
                )
            if observations.pattern is not None and observations.pattern not in PATTERNS:
                raise CalendarAnimError(
                    f"Unknown calibration pattern in observations: {observations.pattern}"
                )
            if pattern is not None and observations.pattern != pattern:
                raise CalendarAnimError(
                    f"Observation pattern {observations.pattern!r} does not match "
                    f"--pattern {pattern!r}"
                )
            path = output or Path("output/calibration") / run_id / "calibration-observations.yaml"
            write_observations(observations, path)
            resolved_profile_path = profile_output or (
                DEFAULT_PROFILE_PATH
                if output is None
                else path.with_name("calibration-profile.yaml")
            )
            profile = apply_observations(load_profile(resolved_profile_path), observations)
            save_profile(profile, resolved_profile_path)
            typer.echo(f"Observations: {path}")
            typer.echo(f"Calibration profile: {resolved_profile_path}")
            return
        if pattern is not None and pattern not in PATTERNS:
            raise CalendarAnimError(f"Unknown calibration pattern: {pattern}")
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
        parsed_preferred_colors = _color_ids(preferred_color_ids, "--preferred-color-ids")
        parsed_poor_contrast_colors = _color_ids(
            poor_contrast_color_ids, "--poor-contrast-color-ids"
        )
        parsed_similar_groups = _similar_color_groups(similar_color_groups)
        if recommended_color_count is not None and not 1 <= recommended_color_count <= len(
            EVENT_COLORS
        ):
            raise CalendarAnimError(
                f"--recommended-color-count must be between 1 and {len(EVENT_COLORS)}"
            )
        if week_starts_on is not None:
            week_starts_on = week_starts_on.lower()
            valid_days = {
                "monday",
                "tuesday",
                "wednesday",
                "thursday",
                "friday",
                "saturday",
                "sunday",
            }
            if week_starts_on not in valid_days:
                raise CalendarAnimError("--week-starts-on must be an English weekday name")
        if maximum_useful_bar_width is not None and maximum_useful_bar_width > 6:
            raise CalendarAnimError("--maximum-useful-bar-width cannot exceed the tested width 6")
        if recommended_horizontal_strategy is not None:
            recommended_horizontal_strategy = recommended_horizontal_strategy.strip()
            if not recommended_horizontal_strategy:
                raise CalendarAnimError("--recommended-horizontal-strategy cannot be empty")
        parsed_forward_order = _slot_order(visual_order_forward, "--visual-order-forward")
        parsed_reverse_order = _slot_order(visual_order_reverse, "--visual-order-reverse")
        parsed_shuffled_order = _slot_order(visual_order_shuffled, "--visual-order-shuffled")
        if recommended_slot_order_strategy is not None:
            recommended_slot_order_strategy = recommended_slot_order_strategy.strip().lower()
            supported_slot_strategies = {
                "creation-order",
                "summary-prefix",
                "stable-alternative",
                "unusable",
                "none",
            }
            if recommended_slot_order_strategy not in supported_slot_strategies:
                raise CalendarAnimError(
                    "--recommended-slot-order-strategy must be one of: "
                    + ", ".join(sorted(supported_slot_strategies))
                )
        if ordering_controlling_property is not None:
            ordering_controlling_property = ordering_controlling_property.strip().lower()
            supported_controlling_properties = {"summary", "color_id", "unknown"}
            if ordering_controlling_property not in supported_controlling_properties:
                raise CalendarAnimError(
                    "--ordering-controlling-property must be one of: "
                    + ", ".join(sorted(supported_controlling_properties))
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
                "tested_color_ids": (
                    [color_id for color_id, _ in EVENT_COLORS]
                    if pattern == "color-palette"
                    else None
                ),
                "preferred_color_ids": parsed_preferred_colors,
                "recommended_color_count": recommended_color_count,
                "poor_contrast_color_ids": parsed_poor_contrast_colors,
                "similar_color_groups": parsed_similar_groups,
                "week_alignment_ok": week_alignment_ok,
                "timezone_alignment_ok": timezone_alignment_ok,
                "day_alignment_ok": day_alignment_ok,
                "vertical_alignment_ok": vertical_alignment_ok,
                "week_starts_on": week_starts_on,
                "independent_cells_appear_contiguous": (independent_cells_appear_contiguous),
                "visible_gaps_between_cells": visible_gaps_between_cells,
                "same_color_cells_merge_visually": same_color_cells_merge_visually,
                "maximum_useful_bar_width": maximum_useful_bar_width,
                "partial_bar_positioning_predictable": (partial_bar_positioning_predictable),
                "recommended_horizontal_strategy": recommended_horizontal_strategy,
                "visual_order_forward": parsed_forward_order,
                "visual_order_reverse": parsed_reverse_order,
                "visual_order_shuffled": parsed_shuffled_order,
                "stable_after_refresh": stable_after_refresh,
                "stable_after_navigation": stable_after_navigation,
                "stable_after_reopen": stable_after_reopen,
                "creation_order_controls_layout": creation_order_controls_layout,
                "recommended_slot_order_strategy": recommended_slot_order_strategy,
                "ordering_factor_tested": ordering_factor_tested,
                "ordering_controlling_property": ordering_controlling_property,
                "ordering_factor_stable": ordering_factor_stable,
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


def map_frame_command(
    manifest_path: Annotated[Path, typer.Argument(help="animation.json path.")],
    frame_index: Annotated[int, typer.Option("--frame", min=0)] = 0,
    calibration_profile: Annotated[
        Path, typer.Option("--calibration-profile", "--profile")
    ] = DEFAULT_PROFILE_PATH,
    start_date_value: Annotated[
        str | None,
        typer.Option("--start-date", help="Any date in the target frame week (YYYY-MM-DD)."),
    ] = None,
    run_id: Annotated[str | None, typer.Option("--run-id")] = None,
    output: Annotated[Path | None, typer.Option("--output", "-o")] = None,
    fit: Annotated[str, typer.Option("--fit")] = "contain",
    mapping_mode: Annotated[
        FrameMappingMode,
        typer.Option(
            "--mapping-mode",
            help="Cell generation mode: sparse or full-grid.",
        ),
    ] = FrameMappingMode.FULL_GRID,
    event_compression: Annotated[
        EventCompressionMode,
        typer.Option(
            "--event-compression",
            help=(
                "Calendar event compression strategy. The production default is "
                "synchronized-horizontal-bands; use none for baseline/debug behavior."
            ),
        ),
    ] = DEFAULT_EVENT_COMPRESSION,
    calendar_background_color_id: Annotated[
        str | None,
        typer.Option(
            "--calendar-background-color-id",
            help="Calendar colorId used by full-grid structural background cells (default: 8).",
        ),
    ] = None,
    subcolumn_ordering: Annotated[
        SubcolumnOrderStrategy | None,
        typer.Option(
            "--subcolumn-ordering",
            help=(
                "Summary ordering for full-grid plans. Default: zero-width; "
                "use numeric for a visible debug/baseline key."
            ),
        ),
    ] = None,
    max_events: Annotated[int, typer.Option("--max-events", min=1)] = (
        DEFAULT_SINGLE_FRAME_MAX_EVENTS
    ),
    calendar_name: Annotated[str, typer.Option("--calendar-name")] = DEFAULT_CALENDAR_NAME,
    execute: Annotated[
        bool, typer.Option("--execute", help="Upload only this frame to the lab calendar.")
    ] = False,
    yes: Annotated[bool, typer.Option("--yes", help="Skip confirmation with --execute.")] = False,
) -> None:
    """Map exactly one manifest frame; dry-run is fully local by default."""
    try:
        if max_events > ABSOLUTE_SINGLE_FRAME_MAX_EVENTS:
            raise CalendarAnimError(
                f"--max-events cannot exceed the absolute safety limit of "
                f"{ABSOLUTE_SINGLE_FRAME_MAX_EVENTS}"
            )
        if fit != "contain":
            raise CalendarAnimError(f"Unsupported frame fit: {fit}")
        fit_mode: FitMode = "contain"
        manifest = read_manifest(manifest_path)
        errors = validate_manifest_files(manifest, manifest_path.resolve())
        if errors:
            raise CalendarAnimError("Manifest validation failed: " + "; ".join(errors))
        selected_frame = select_frame(manifest, frame_index)
        profile = load_profile(calibration_profile)
        if execute and start_date_value is None:
            raise CalendarAnimError("--start-date is required with --execute")
        anchor_date = date.fromisoformat(start_date_value) if start_date_value else date.today()
        mode_suffix = "" if mapping_mode is FrameMappingMode.SPARSE else "-full-grid"
        if event_compression is EventCompressionMode.SYNCHRONIZED_HORIZONTAL_BANDS:
            mode_suffix += "-bands"
        default_run_id = f"frame-{frame_index:03d}-{manifest.animation_id}{mode_suffix}"[:64]
        resolved_run_id = _valid_identifier(run_id or default_run_id, "run-id")
        plan = build_single_frame_plan(
            manifest,
            profile,
            frame_index=frame_index,
            anchor_date=anchor_date,
            run_id=resolved_run_id,
            max_execute_events=max_events,
            fit=fit_mode,
            calendar_name=calendar_name,
            mapping_mode=mapping_mode,
            event_compression=event_compression,
            calendar_background_color_id=calendar_background_color_id,
            subcolumn_order_strategy=subcolumn_ordering,
        )
        output_dir = output or Path("output/frame-mapping") / plan.run_id
        source_image = manifest_path.resolve().parent / selected_frame.image
        write_frame_mapping_artifacts(plan, source_image, output_dir)
    except (CalendarAnimError, OSError, ValueError) as error:
        _fail(error)

    stats = plan.statistics
    typer.echo(f"Animation ID: {plan.animation_id}")
    typer.echo(f"Run ID: {plan.run_id}")
    typer.echo(f"Frame: {plan.frame_index}")
    typer.echo(f"Mapping mode: {plan.mapping_mode.value}")
    typer.echo(f"Event compression: {plan.event_compression.value}")
    typer.echo(f"Week start: {plan.week_start_date}")
    typer.echo(f"Source grid: {plan.source_grid_width}x{plan.source_grid_height}")
    typer.echo(f"Target grid: {plan.target_grid_width}x{plan.target_grid_height}")
    typer.echo(f"Source blocks: {stats.source_blocks}")
    typer.echo(f"Expanded logical cells: {stats.expanded_logical_cells}")
    typer.echo(f"Foreground cells: {stats.foreground_cells_after_fitting}")
    typer.echo(f"Background structural cells: {stats.background_structural_cells}")
    typer.echo(f"Mapped cells: {stats.total_logical_cells}")
    typer.echo(f"Calendar events: {stats.calendar_events} / {plan.max_execute_events}")
    typer.echo(f"Baseline Calendar events: {stats.baseline_calendar_events}")
    typer.echo(f"Saved Calendar events: {stats.saved_calendar_events}")
    reduction_percent = (
        (stats.saved_calendar_events / stats.baseline_calendar_events) * 100
        if stats.baseline_calendar_events
        else 0
    )
    typer.echo(f"Compression reduction: {reduction_percent:.1f}%")
    typer.echo(f"Foreground events: {stats.foreground_events}")
    typer.echo(f"Background events: {stats.background_events}")
    typer.echo(f"Foreground Calendar colors: {stats.foreground_calendar_colors}")
    typer.echo(f"Background colorId: {plan.background_color_id or 'not used'}")
    typer.echo(f"Subcolumn ordering: {plan.subcolumn_order_strategy.value}")
    typer.echo(
        "Subcolumn slot keys: "
        + (
            ", ".join(format_summary_key(key) for key in plan.subcolumn_order_keys)
            if plan.subcolumn_order_keys
            else "not used"
        )
    )
    typer.echo(f"Mapper readiness: {'READY' if plan.profile_ready else 'NOT READY'}")
    typer.echo(f"Execution: {'REAL' if execute else 'DRY RUN'}")
    typer.echo(f"Artifacts: {output_dir}")
    for warning in plan.warnings:
        typer.secho(f"Warning: {warning}", fg=typer.colors.YELLOW)
    if not execute:
        if yes:
            typer.echo("--yes has no effect without --execute; no API call was made.")
        return
    if not plan.profile_ready:
        blockers = list(profile.missing_mapper_calibrations)
        if (
            plan.mapping_mode is FrameMappingMode.FULL_GRID
            and not profile.subcolumn_order_mapping.strategy_ready(plan.subcolumn_order_strategy)
        ):
            blockers.append(f"confirmed {plan.subcolumn_order_strategy.value} mapper strategy")
        if (
            plan.event_compression is EventCompressionMode.SYNCHRONIZED_HORIZONTAL_BANDS
            and not synchronized_horizontal_bands_ready(profile)
        ):
            blockers.append("synchronized horizontal-bands calibration")
        missing = ", ".join(blockers)
        _fail(CalendarAnimError(f"Calibration profile is NOT READY; missing: {missing}"))
    if plan.event_count > plan.max_execute_events:
        _fail(
            CalendarAnimError(
                f"Frame requires {plan.event_count} events, above the configured execute "
                f"limit of {plan.max_execute_events}"
            )
        )
    if not yes:
        typer.echo(f"\nMapping mode: {plan.mapping_mode.value.upper()}")
        typer.echo(f"Event compression: {plan.event_compression.value}")
        typer.echo(f"Subcolumn ordering: {plan.subcolumn_order_strategy.value}")
        typer.echo(f"Target grid: {plan.target_grid_width}x{plan.target_grid_height}")
        typer.echo(f"Foreground events: {stats.foreground_events}")
        typer.echo(f"Background events: {stats.background_events}")
        typer.echo(f"Total events: {stats.calendar_events}")
        typer.echo(f"Calendar: {plan.calendar_name}")
        typer.echo(f"Frame: {plan.frame_index}")
        typer.echo(f"Run ID: {plan.run_id}")
        typer.echo(f"\nThis will create {stats.calendar_events} real Google Calendar events.")
        typer.confirm("Continue?", default=False, abort=True)
    try:
        gateway = _google_gateway()
        service = SingleFrameMappingService(
            gateway,
            LabCalendarService(gateway, CalendarConfigStore()),
        )
        result = service.execute(plan)
        write_frame_execution_result(result, output_dir)
    except (CalendarAnimError, HttpError, OSError) as error:
        _fail(error)
    typer.echo(f"Calendar ID: {result.calendar_id}")
    typer.echo(f"Planned events: {result.planned_events}")
    typer.echo(f"Created events: {result.created_events}")
    typer.echo(f"Foreground created: {result.foreground_created}")
    typer.echo(f"Background created: {result.background_created}")
    typer.echo(f"Failed events: {result.failed_events}")
    for result_error in result.errors:
        typer.secho(f"Error: {result_error}", fg=typer.colors.RED, err=True)
    if result.failed_events:
        raise typer.Exit(code=1)


def register_calendar_commands(app: typer.Typer) -> None:
    app.command("calibration-patterns")(calibration_patterns_command)
    app.command("calibrate")(calibrate_command)
    app.command("cleanup")(cleanup_command)
    app.command("lab-info")(lab_info_command)
    app.command("record-calibration")(record_calibration_command)
    app.command("calibration-summary")(calibration_summary_command)
    app.command("map-frame")(map_frame_command)
    register_multi_frame_commands(app)
    register_recurrence_compaction_commands(app)
    register_capture_commands(app)
    register_vertical_compression_commands(app)
    register_horizontal_band_compression_commands(app)
