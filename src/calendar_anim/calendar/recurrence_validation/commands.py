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
from calendar_anim.calendar.recurrence_validation.ordering import (
    ORDERING_START_WEEK,
    ORDERING_VALIDATION_ID,
    OrderingCaptureResult,
    OrderingDomEvent,
    OrderingDomSnapshot,
    OrderingValidationStore,
    RecurrenceOrderingValidationPlan,
    analyze_snapshots,
    build_ordering_validation_plan,
    compose_ordering_comparison,
    extract_rendered_slot_colors,
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


def prepare_recurrence_ordering_validation_command(
    validation_id: Annotated[str, typer.Option("--validation-id")] = ORDERING_VALIDATION_ID,
    source_run_id: Annotated[str, typer.Option("--source-run-id")] = DEFAULT_SOURCE_RUN_ID,
    start_week_value: Annotated[str, typer.Option("--start-week")] = str(ORDERING_START_WEEK),
    profile_name: Annotated[str, typer.Option("--profile")] = "account-b",
    calendar_name: Annotated[str, typer.Option("--calendar-name")] = "Calendar Animation Lab B",
    animation_output_root: Annotated[Path, typer.Option("--animation-output-root")] = Path(
        "output/animation-runs"
    ),
    output_root: Annotated[Path, typer.Option("--output-root")] = Path(
        "output/recurrence-validation"
    ),
) -> None:
    """Prepare the isolated 18-slot recurrence ordering validation locally."""

    try:
        plan = build_ordering_validation_plan(
            AnimationRunStore(animation_output_root),
            validation_id=_validation_id(validation_id),
            source_run_id=source_run_id,
            start_week=date.fromisoformat(start_week_value),
            calendar_profile=profile_name,
            calendar_name=calendar_name,
        )
        store = OrderingValidationStore(output_root)
        path = store.save_plan(plan)
    except (CalendarAnimError, OSError, ValueError) as error:
        _fail(error)
    typer.echo(f"Validation: {plan.validation_id}")
    typer.echo(f"PROFILE: {plan.calendar_profile}")
    typer.echo(f"CALENDAR: {plan.calendar_name}")
    typer.echo("Recurring: 18 parents x 3 displayed weeks")
    typer.echo("Standalone controls: 18")
    typer.echo("EXPECTED INSERTS: 36")
    typer.echo(f"Weeks: {plan.first_week} through {plan.last_week}")
    typer.echo(f"Plan: {path}")
    typer.echo("Google Calendar calls: NO")


def _ordering_profile_calendar(
    plan: RecurrenceOrderingValidationPlan,
) -> tuple[CalendarAccountProfile, GoogleRecurrenceValidationGateway, CalendarInfo]:
    profiles = _profile_service()
    profile, base_gateway = profiles.gateway(plan.calendar_profile)
    if not isinstance(base_gateway, GoogleRecurrenceValidationGateway):
        raise CalendarAnimError("Profile gateway does not support recurrence validation")
    calendar = LabCalendarService(
        base_gateway,
        ProfileCalendarConfigStore(profiles.store, plan.calendar_profile),
    ).find(plan.calendar_name)
    if calendar is None:
        raise CalendarAnimError("Configured laboratory calendar was not found")
    if calendar.access_role != "owner":
        raise CalendarAnimError("Account B does not own the ordering validation calendar")
    if profile.calendar_id != calendar.id:
        raise CalendarAnimError("Profile calendar ID changed during validation preflight")
    return profile, base_gateway, calendar


def upload_recurrence_ordering_validation_command(
    validation_id: Annotated[str, typer.Option("--validation-id")] = ORDERING_VALIDATION_ID,
    profile_name: Annotated[str, typer.Option("--profile")] = "account-b",
    output_root: Annotated[Path, typer.Option("--output-root")] = Path(
        "output/recurrence-validation"
    ),
    execute: Annotated[bool, typer.Option("--execute")] = False,
) -> None:
    """Preflight clean Account-B weeks and create at most 36 validation resources."""

    try:
        store = OrderingValidationStore(output_root)
        plan = store.load_plan(_validation_id(validation_id))
        selected = _selected_profile(profile_name, plan.calendar_profile)
    except (CalendarAnimError, OSError, ValueError) as error:
        _fail(error)
    typer.echo(f"PROFILE: {selected}")
    typer.echo(f"CALENDAR: {plan.calendar_name}")
    typer.echo(f"VALIDATION ID: {plan.validation_id}")
    typer.echo("EXPECTED INSERTS: 36")
    typer.echo("Preflight: abort if unrelated events exist in any validation week")
    typer.echo(f"EXECUTION: {'REAL' if execute else 'DRY RUN'}")
    if not execute:
        typer.echo("No authentication or Calendar API call was performed.")
        return
    try:
        profile, gateway, calendar = _ordering_profile_calendar(plan)
        typer.echo(f"ACCOUNT: {profile.authenticated_google_account}")
        typer.echo(f"CALENDAR ID: {calendar.id}")
        typer.confirm("Create only these 36 metadata-scoped Account-B resources?", abort=True)
        state = RecurrenceValidationService(
            gateway, store, lambda _name, _timezone: (calendar, False)
        ).upload(plan)
    except (CalendarAnimError, HttpError, OSError, ValueError) as error:
        _fail(error)
    typer.echo(f"Status: {state.status.value}")
    typer.echo(f"events.insert calls: {state.events_insert_calls}")
    typer.echo(f"rateLimitExceeded: {state.rate_limit_exceeded_count}")
    typer.echo(f"quotaExceeded: {state.quota_exceeded_count}")


def _ordering_snapshot(
    gateway: PlaywrightCalendarCaptureGateway,
    plan: RecurrenceOrderingValidationPlan,
    *,
    label: str,
    week_start: date,
) -> OrderingDomSnapshot:
    raw = gateway.collect_zero_width_event_geometry(plan.summaries, plan.color_ids)
    by_slot: dict[int, OrderingDomEvent] = {}
    for item in raw:
        event = OrderingDomEvent.model_validate(item)
        current = by_slot.get(event.slot_index)
        event_score = (
            event.rendered_color is None,
            event.width * event.height,
        )
        current_score = (
            (
                current.rendered_color is None,
                current.width * current.height,
            )
            if current is not None
            else None
        )
        if current_score is None or event_score < current_score:
            by_slot[event.slot_index] = event
    events = list(by_slot.values())
    ordered = sorted(events, key=lambda item: (item.x, item.slot_index))
    slot_order = [item.slot_index for item in ordered]
    return OrderingDomSnapshot(
        label=label,
        week_start=week_start,
        events=events,
        summaries_preserved=len(events) == 18
        and all(event.summary == plan.summaries[event.slot_index] for event in events),
        strictly_increasing_x=len(ordered) == 18
        and all(left.x < right.x for left, right in zip(ordered, ordered[1:], strict=False)),
        slot_order=slot_order,
    )


def capture_recurrence_ordering_validation_command(
    validation_id: Annotated[str, typer.Option("--validation-id")] = ORDERING_VALIDATION_ID,
    profile_name: Annotated[str, typer.Option("--profile")] = "account-b",
    output_root: Annotated[Path, typer.Option("--output-root")] = Path(
        "output/recurrence-validation"
    ),
    profile_directory: Annotated[Path | None, typer.Option("--profile-directory")] = None,
    execute: Annotated[bool, typer.Option("--execute")] = False,
) -> None:
    """Capture ordering, DOM geometry, refresh, and navigation stability."""

    try:
        store = OrderingValidationStore(output_root)
        plan = store.load_plan(_validation_id(validation_id))
        selected = _selected_profile(profile_name, plan.calendar_profile)
        profile = CalendarProfileStore().load(selected)
        state = store.load_state(plan.validation_id)
    except (CalendarAnimError, OSError, ValueError) as error:
        _fail(error)
    typer.echo(f"Validation: {plan.validation_id}")
    typer.echo(f"Profile: {selected}")
    typer.echo(f"Browser zoom: {profile.capture_zoom_percent}%")
    typer.echo("Positioning: required Calendar vertical scroller, 06:00-00:00")
    typer.echo("DOM analysis: exact Unicode + x/width/y/height + CSS color")
    typer.echo(f"Execution: {'REAL BROWSER' if execute else 'DRY RUN'}")
    if not execute:
        typer.echo("No browser was opened and no Calendar API call was made.")
        return
    if state is None or state.status.value != "completed":
        _fail(CalendarAnimError("Ordering validation upload must be completed before capture"))
    if profile.capture_zoom_percent != 90:
        _fail(CalendarAnimError("Account B ordering capture is locked to 90% zoom"))
    recurring = plan.recurring_weeks[0]
    away = plan.recurring_weeks[1]
    snapshots: list[OrderingDomSnapshot] = []
    try:
        with PlaywrightCalendarCaptureGateway(
            _recurrence_capture_config(profile, profile_directory)
        ) as gateway:
            gateway.open_week(recurring)
            gateway.wait_until_ready(recurring, 18)
            gateway.capture(store.screenshot_path(plan.validation_id, "recurring-initial"))
            snapshots.append(
                _ordering_snapshot(gateway, plan, label="recurring-initial", week_start=recurring)
            )
            gateway.reload_current_week(recurring, 18)
            gateway.capture(store.screenshot_path(plan.validation_id, "recurring-refresh"))
            snapshots.append(
                _ordering_snapshot(gateway, plan, label="recurring-refresh", week_start=recurring)
            )
            gateway.open_week(away)
            gateway.wait_until_ready(away, 18)
            snapshots.append(
                _ordering_snapshot(gateway, plan, label="recurring-week-2", week_start=away)
            )
            gateway.open_week(recurring)
            gateway.wait_until_ready(recurring, 18)
            gateway.capture(store.screenshot_path(plan.validation_id, "recurring-navigation"))
            snapshots.append(
                _ordering_snapshot(
                    gateway, plan, label="recurring-navigation", week_start=recurring
                )
            )
            third = plan.recurring_weeks[2]
            gateway.open_week(third)
            gateway.wait_until_ready(third, 18)
            snapshots.append(
                _ordering_snapshot(gateway, plan, label="recurring-week-3", week_start=third)
            )
            gateway.open_week(plan.standalone_week)
            gateway.wait_until_ready(plan.standalone_week, 18)
            gateway.capture(store.screenshot_path(plan.validation_id, "standalone"))
            snapshots.append(
                _ordering_snapshot(
                    gateway, plan, label="standalone", week_start=plan.standalone_week
                )
            )
        comparison = compose_ordering_comparison(plan, store)
        rendered_colors = {
            label: extract_rendered_slot_colors(
                store.screenshot_path(plan.validation_id, label), snapshots
            )
            for label in ("recurring-initial", "standalone")
        }
        result = analyze_snapshots(
            plan,
            snapshots,
            comparison,
            rendered_colors,
            "existing-screenshot-pixels",
        )
        report_path = store.save_capture_result(result)
    except (CalendarAnimError, OSError, RuntimeError, ValueError) as error:
        _fail(error)
    typer.echo(f"RECURRENCE ZERO-WIDTH ORDERING = {result.result}")
    typer.echo(f"18/18 summaries: {'YES' if result.summaries_preserved_18_of_18 else 'NO'}")
    typer.echo(f"Strict x ordering: {'YES' if result.strict_x_ordering else 'NO'}")
    typer.echo(f"Recurring == standalone: {'YES' if result.recurring_equals_standalone else 'NO'}")
    typer.echo(f"Refresh stable: {'YES' if result.refresh_stable else 'NO'}")
    typer.echo(f"Navigation stable: {'YES' if result.navigation_stable else 'NO'}")
    typer.echo(f"Rendered colors match: {'YES' if result.rendered_colors_match else 'NO'}")
    typer.echo(f"Expected color mapping verified: {result.expected_color_mapping_verified.value}")
    typer.echo(f"Comparison: {comparison}")
    typer.echo(f"DOM report: {report_path}")
    typer.echo("Calendar writes during capture: NO")


def reprocess_recurrence_ordering_validation_command(
    validation_id: Annotated[str, typer.Option("--validation-id")] = ORDERING_VALIDATION_ID,
    output_root: Annotated[Path, typer.Option("--output-root")] = Path(
        "output/recurrence-validation"
    ),
) -> None:
    """Re-evaluate existing screenshots and DOM artifacts without opening Calendar."""

    try:
        store = OrderingValidationStore(output_root)
        plan = store.load_plan(_validation_id(validation_id))
        report_path = store.capture_report_path(plan.validation_id)
        original_report = report_path.read_text(encoding="utf-8")
        old = OrderingCaptureResult.model_validate_json(original_report)
        snapshots = old.snapshots
        rendered_colors = {
            label: extract_rendered_slot_colors(
                store.screenshot_path(plan.validation_id, label), snapshots
            )
            for label in ("recurring-initial", "standalone")
        }
        result = analyze_snapshots(
            plan,
            snapshots,
            Path(old.comparison_path),
            rendered_colors,
            "existing-screenshot-pixels",
        )
        backup = report_path.with_name("ordering-result.pre-color-fix.json")
        if not backup.exists():
            backup.write_text(original_report, encoding="utf-8")
        store.save_capture_result(result)
    except (CalendarAnimError, OSError, ValueError) as error:
        _fail(error)
    typer.echo(f"Old result: {old.result}")
    typer.echo(f"New result: {result.result}")
    typer.echo(
        "Recurring vs standalone rendered colors: "
        f"{sum(item.match for item in result.color_comparisons)}/18 match"
    )
    for item in result.color_comparisons:
        typer.echo(
            f"Slot {item.slot_index:02d} colorId={item.expected_color_id} "
            f"recurring={item.recurring_rendered_color} "
            f"standalone={item.standalone_rendered_color} "
            f"match={'YES' if item.match else 'NO'}"
        )
    typer.echo(f"Expected color mapping verified: {result.expected_color_mapping_verified.value}")
    typer.echo(f"Report: {store.capture_report_path(plan.validation_id)}")
    typer.echo("Browser opened: NO")
    typer.echo("Google Calendar reads: NO")
    typer.echo("Google Calendar writes: NO")


def cleanup_recurrence_ordering_validation_command(
    validation_id: Annotated[str, typer.Option("--validation-id")] = ORDERING_VALIDATION_ID,
    profile_name: Annotated[str, typer.Option("--profile")] = "account-b",
    output_root: Annotated[Path, typer.Option("--output-root")] = Path(
        "output/recurrence-validation"
    ),
    execute: Annotated[bool, typer.Option("--execute")] = False,
) -> None:
    """Delete only the metadata-scoped Account-B ordering validation."""

    try:
        store = OrderingValidationStore(output_root)
        plan = store.load_plan(_validation_id(validation_id))
        _selected_profile(profile_name, plan.calendar_profile)
    except (CalendarAnimError, OSError, ValueError) as error:
        _fail(error)
    typer.echo(f"PROFILE: {plan.calendar_profile}")
    typer.echo(f"VALIDATION ID: {plan.validation_id}")
    typer.echo("Filter: generated_by + validation_id + calendar_profile")
    typer.echo(f"Execution: {'REAL DELETE' if execute else 'DRY RUN'}")
    if not execute:
        typer.echo("No authentication, Calendar lookup, or deletion was performed.")
        return
    try:
        profile, gateway, calendar = _ordering_profile_calendar(plan)
        typer.echo(f"ACCOUNT: {profile.authenticated_google_account}")
        typer.echo(f"CALENDAR: {calendar.name}")
        typer.confirm("Delete only this Account-B ordering validation?", abort=True)
        result = RecurrenceValidationService(
            gateway, store, lambda _name, _timezone: (calendar, False)
        ).cleanup(plan, calendar)
    except (CalendarAnimError, HttpError, OSError, ValueError) as error:
        _fail(error)
    typer.echo(f"Matched resources: {len(result.matched_resource_ids)}")
    typer.echo(f"Deleted resources: {result.deleted_resources}")
    typer.echo(f"Failed deletions: {result.failed_deletions}")


def register_recurrence_validation_commands(app: typer.Typer) -> None:
    app.command("prepare-recurrence-validation")(prepare_recurrence_validation_command)
    app.command("upload-recurrence-validation")(upload_recurrence_validation_command)
    app.command("capture-recurrence-validation")(capture_recurrence_validation_command)
    app.command("cleanup-recurrence-validation")(cleanup_recurrence_validation_command)
    app.command("prepare-recurrence-ordering-validation")(
        prepare_recurrence_ordering_validation_command
    )
    app.command("upload-recurrence-ordering-validation")(
        upload_recurrence_ordering_validation_command
    )
    app.command("capture-recurrence-ordering-validation")(
        capture_recurrence_ordering_validation_command
    )
    app.command("reprocess-recurrence-ordering-validation")(
        reprocess_recurrence_ordering_validation_command
    )
    app.command("cleanup-recurrence-ordering-validation")(
        cleanup_recurrence_ordering_validation_command
    )
