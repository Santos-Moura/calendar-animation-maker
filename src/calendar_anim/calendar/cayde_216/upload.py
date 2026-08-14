import re
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Annotated, Never
from zoneinfo import ZoneInfo

import typer
from googleapiclient.errors import HttpError

from calendar_anim.calendar.cayde_216.artifacts import Cayde216Store
from calendar_anim.calendar.cayde_216.planner import (
    CHUNK_SIZE,
    EXPECTED_INPUT_SHA256,
    FIRST_WEEK,
    FRAME_COUNT,
    RUN_ID,
)
from calendar_anim.calendar.event_identity import deterministic_event_id
from calendar_anim.calendar.multi_frame.quota_wait import QuotaWaitPolicy
from calendar_anim.calendar.profiles.service import CalendarProfileService
from calendar_anim.calendar.profiles.store import CalendarProfileStore
from calendar_anim.calendar.recurrence_compaction.models import RecurrenceMigrationPlan
from calendar_anim.calendar.recurrence_upload.artifacts import (
    RecurrenceUploadStore,
    build_dry_run_report,
    file_sha256,
    performance_from_state,
)
from calendar_anim.calendar.recurrence_upload.gateway import GoogleRecurrenceUploadGateway
from calendar_anim.calendar.recurrence_upload.models import RecurrenceDryRunReport
from calendar_anim.calendar.recurrence_upload.service import RecurrenceUploadService
from calendar_anim.exceptions import CalendarAnimError

RDATE_PATTERN = re.compile(r"^RDATE;TZID=([^:]+):(.+)$")
UPLOAD_ARTIFACT_NAMES = (
    "animation-plan.json",
    "recurrence-plan.json",
    "recurrence-report.json",
    "sizing-report.json",
    "remote-preflight.json",
)


def upload_store() -> RecurrenceUploadStore:
    return RecurrenceUploadStore(
        Path("output/216-plans"),
        Path("output/216-runs"),
        artifact_names=UPLOAD_ARTIFACT_NAMES,
        recurrence_plan_name="recurrence-plan.json",
        recurrence_report_name="recurrence-report.json",
    )


def validate_cayde_216_upload(
    run_id: str,
    input_file: Path,
) -> tuple[RecurrenceMigrationPlan, RecurrenceDryRunReport, dict[str, str]]:
    if run_id != RUN_ID:
        raise CalendarAnimError(f"Cayde 216 upload requires locked final run ID {RUN_ID}")
    store = upload_store()
    hashes = store.artifact_hashes(run_id)
    plan = store.load_plan(run_id)
    study = store.load_report(run_id)
    animation = store.load_json(run_id, "animation-plan.json")
    sizing = store.load_json(run_id, "sizing-report.json")
    preflight = store.load_json(run_id, "remote-preflight.json")
    if file_sha256(input_file) != EXPECTED_INPUT_SHA256:
        raise CalendarAnimError("input.mp4 differs from the approved source")
    expected_animation = {
        "run_id": run_id,
        "calendar_profile": "account-b",
        "calendar_name": "Calendar Animation Lab B",
        "frame_start": 0,
        "frame_count": 216,
        "output_fps": 6.0,
        "start_week": FIRST_WEEK.isoformat(),
        "target_grid_width": 126,
        "target_grid_height": 72,
        "palette_preset": "cayde-cyan-magenta",
        "background_color_id": "7",
        "foreground_color_ids": ["3", "5", "9", "11"],
        "event_compression": "synchronized-horizontal-bands",
        "subcolumn_order_strategy": "zero-width",
    }
    for key, expected in expected_animation.items():
        if animation.get(key) != expected:
            raise CalendarAnimError(f"Cayde 216 final animation invariant changed: {key}")
    expected_sizing = {
        "first_week": FIRST_WEEK.isoformat(),
        "last_week": (FIRST_WEEK + timedelta(weeks=215)).isoformat(),
        "palette_preset": "cayde-cyan-magenta",
        "background_color_id": "7",
        "foreground_color_ids": ["3", "5", "9", "11"],
        "expansion_exact": True,
        "parent_ids_unique": True,
        "parent_id_collisions_with_existing_b": 0,
        "old_week_overlap": 0,
    }
    for key, expected in expected_sizing.items():
        if sizing.get(key) != expected:
            raise CalendarAnimError(f"Cayde 216 sizing gate changed: {key}")
    expected_preflight = {
        "profile": "account-b",
        "calendar_name": "Calendar Animation Lab B",
        "timezone": "America/Sao_Paulo",
        "range_start": FIRST_WEEK.isoformat(),
        "range_end_exclusive": (FIRST_WEEK + timedelta(weeks=FRAME_COUNT)).isoformat(),
        "unexpected_event_count": 0,
        "new_range_clean": True,
        "old_artifacts_unchanged": True,
        "google_calendar_writes": False,
        "result": "PASS",
    }
    for key, expected in expected_preflight.items():
        if preflight.get(key) != expected:
            raise CalendarAnimError(f"Cayde 216 final preflight gate changed: {key}")
    if plan.parent_chunk_size != CHUNK_SIZE or len(plan.parents) != sizing.get("recurring_parents"):
        raise CalendarAnimError("Cayde 216 parent count/chunk differs from sizing report")
    _validate_parent_serialization(plan)
    expected_keys = _expected_occurrence_keys(Cayde216Store(), animation)
    report = build_dry_run_report(
        run_id,
        plan,
        study,
        hashes,
        EXPECTED_INPUT_SHA256,
        expected_occurrence_keys=expected_keys,
    )
    if not report.expansion_equality or not report.unique_parent_ids:
        raise CalendarAnimError("Cayde 216 recurrence expansion/ID gate failed")
    return plan, report, hashes


def upload_cayde_216_recurrence_command(
    run_id: Annotated[str, typer.Option("--run-id")] = RUN_ID,
    profile: Annotated[str, typer.Option("--profile")] = "account-b",
    input_file: Annotated[Path, typer.Option("--input")] = Path("input.mp4"),
    resume: Annotated[bool, typer.Option("--resume")] = False,
    execute: Annotated[bool, typer.Option("--execute")] = False,
) -> None:
    """Upload only the approved final Cyan Magenta recurrence plan, when explicitly run."""

    if profile != "account-b":
        _fail(CalendarAnimError("Cayde 216 recurrence upload is restricted to account-b"))
    try:
        plan, report, hashes = validate_cayde_216_upload(run_id, input_file)
        store = upload_store()
        state = store.initialize_state(run_id, plan, hashes, 1.0)
        store.save_dry_run(report)
        store.save_performance(performance_from_state(state, 0))
    except (CalendarAnimError, OSError, ValueError) as error:
        _fail(error)
    _print_summary(report)
    typer.echo("PALETTE: Cyan Magenta")
    typer.echo(f"WEEKS: {FIRST_WEEK} -> {FIRST_WEEK + timedelta(weeks=215)}")
    typer.echo(f"EXECUTION: {'REAL' if execute else 'DRY RUN'}")
    if not execute:
        typer.echo("No authentication or Calendar API call was performed.")
        typer.echo("Google Calendar writes: NO")
        return
    if not resume:
        _fail(CalendarAnimError("Real Cayde 216 bulk requires --resume"))
    try:
        profiles = CalendarProfileService(
            CalendarProfileStore(), gateway_factory=GoogleRecurrenceUploadGateway
        )
        account, gateway = profiles.gateway(profile)
        if not isinstance(gateway, GoogleRecurrenceUploadGateway) or not account.calendar_id:
            raise CalendarAnimError("Account-B recurrence gateway is unavailable")
        calendar = gateway.get_calendar(account.calendar_id)
        if (
            calendar is None
            or calendar.name != "Calendar Animation Lab B"
            or calendar.access_role != "owner"
            or calendar.timezone != "America/Sao_Paulo"
        ):
            raise CalendarAnimError("Account-B Calendar identity/ownership/timezone mismatch")
        if state.calendar_id not in {None, calendar.id}:
            raise CalendarAnimError("Checkpoint belongs to another Calendar")
        if state.completed_count == 0:
            zone = ZoneInfo("America/Sao_Paulo")
            existing = gateway.list_window(
                calendar.id,
                datetime.combine(FIRST_WEEK, time.min, zone),
                datetime.combine(FIRST_WEEK + timedelta(weeks=FRAME_COUNT), time.min, zone),
            )
            if existing:
                raise CalendarAnimError(
                    f"Final write-time preflight found {len(existing)} event(s); upload stopped"
                )
        typer.echo(f"ACCOUNT: {account.authenticated_google_account}")
        typer.echo(f"CALENDAR ID: {calendar.id}")
        typer.echo("UNATTENDED QUOTA RECOVERY: enabled")
        typer.confirm("Start 43k+ recurring-parent bulk upload?", default=False, abort=True)
        gateway.restore_write_pacing(state.write_pacing)
        service = RecurrenceUploadService(
            gateway,
            store,
            quota_policy=QuotaWaitPolicy(
                cooldown_seconds=(900, 1800, 3600, 7200, 14400),
                jitter_seconds=60,
                max_auto_wait_seconds=48 * 3600,
                conservative_recovery_interval_seconds=1.5,
            ),
        )
        state = service.upload(plan, state, calendar.id)
    except KeyboardInterrupt:
        typer.secho("Interrupted safely; atomic checkpoint preserved.", fg="yellow")
        raise typer.Exit(code=130) from None
    except (CalendarAnimError, HttpError, OSError, ValueError) as error:
        _fail(error)
    typer.echo(f"Completed parents: {state.completed_count}/{len(state.parents)}")


def _expected_occurrence_keys(store: Cayde216Store, animation: dict[str, object]) -> set[str]:
    raw_frames = animation.get("frames")
    if not isinstance(raw_frames, list):
        raise CalendarAnimError("Cayde 216 animation frame list is invalid")
    indices = [int(item["frame_index"]) for item in raw_frames if isinstance(item, dict)]
    if indices != list(range(FRAME_COUNT)):
        raise CalendarAnimError("Cayde 216 frame sequence differs from 0-215")
    plan = store.load_plan(RUN_ID)
    keys = set()
    total = 0
    for frame_index in indices:
        frame = store.load_frame_plan(plan, frame_index)
        for event in frame.events:
            keys.add(f"f{frame_index:04d}:{deterministic_event_id(event)}")
            total += 1
    if len(keys) != total:
        raise CalendarAnimError("Cayde 216 standalone occurrences are duplicated")
    return keys


def _validate_parent_serialization(plan: RecurrenceMigrationPlan) -> None:
    gateway = GoogleRecurrenceUploadGateway(None)
    ids = set()
    for parent in plan.parents:
        if parent.parent_id in ids:
            raise CalendarAnimError(f"Duplicate parent ID: {parent.parent_id}")
        ids.add(parent.parent_id)
        body = gateway.parent_body(parent)
        if body.get("colorId") != parent.signature.color_id:
            raise CalendarAnimError(f"Parent color serialization failed: {parent.parent_id}")
        if parent.occurrence_count > CHUNK_SIZE:
            raise CalendarAnimError(f"Parent exceeds chunk size: {parent.parent_id}")
        for line in parent.recurrence:
            match = RDATE_PATTERN.fullmatch(line)
            if match is None or match.group(1) != plan.timezone:
                raise CalendarAnimError(f"Invalid RDATE: {parent.parent_id}")
            for value in match.group(2).split(","):
                datetime.strptime(value, "%Y%m%dT%H%M%S")


def _print_summary(report: RecurrenceDryRunReport) -> None:
    typer.echo(f"LOGICAL OCCURRENCES: {report.logical_occurrences}")
    typer.echo(f"RECURRING PARENTS: {report.parent_inserts}")
    typer.echo(f"REDUCTION: {report.reduction_percent:.3f}%")
    typer.echo(f"UNIQUE IDs: {'YES' if report.unique_parent_ids else 'NO'}")
    typer.echo(f"EXPANSION EQUALITY: {'YES' if report.expansion_equality else 'NO'}")


def _fail(error: Exception) -> Never:
    typer.secho(f"Error: {error}", fg=typer.colors.RED, err=True)
    raise typer.Exit(code=1)
