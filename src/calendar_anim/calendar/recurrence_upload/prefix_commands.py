from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Annotated, Never
from zoneinfo import ZoneInfo

import typer
from googleapiclient.errors import HttpError

from calendar_anim.calendar.event_identity import deterministic_event_id
from calendar_anim.calendar.multi_frame.artifacts import AnimationRunStore
from calendar_anim.calendar.multi_frame.quota_wait import QuotaWaitPolicy
from calendar_anim.calendar.profiles.service import CalendarProfileService
from calendar_anim.calendar.profiles.store import CalendarProfileStore
from calendar_anim.calendar.recurrence_compaction.account_b_prefix import (
    ACCOUNT_B_PREFIX_RUN_ID,
    AccountBPrefixFinalReport,
    validate_account_b_prefix_report,
    validate_prefix_input_hash,
)
from calendar_anim.calendar.recurrence_upload.artifacts import (
    RecurrenceUploadStore,
    build_dry_run_report,
    performance_from_state,
)
from calendar_anim.calendar.recurrence_upload.commands import (
    INITIAL_WRITE_INTERVAL_SECONDS,
    _print_local_summary,
    _validate_parent_serialization,
)
from calendar_anim.calendar.recurrence_upload.gateway import GoogleRecurrenceUploadGateway
from calendar_anim.calendar.recurrence_upload.service import RecurrenceUploadService
from calendar_anim.exceptions import CalendarAnimError

PREFIX_ARTIFACT_NAMES = (
    "prefix-animation-plan.json",
    "prefix-recurrence-plan.json",
    "prefix-recurrence-report.json",
    "prefix-final-report.json",
    "prefix-final-report.txt",
)


def _fail(error: Exception) -> Never:
    typer.secho(f"Error: {error}", fg=typer.colors.RED, err=True)
    raise typer.Exit(code=1)


def _prefix_store(plan_root: Path, state_root: Path) -> RecurrenceUploadStore:
    return RecurrenceUploadStore(
        plan_root,
        state_root,
        artifact_names=PREFIX_ARTIFACT_NAMES,
        recurrence_plan_name="prefix-recurrence-plan.json",
        recurrence_report_name="prefix-recurrence-report.json",
    )


def _load_and_validate_prefix(store: RecurrenceUploadStore, run_id: str, input_file: Path):  # type: ignore[no-untyped-def]
    if run_id != ACCOUNT_B_PREFIX_RUN_ID:
        raise CalendarAnimError("Only the locked Account-B prefix run may use this uploader")
    validate_prefix_input_hash(input_file)
    plan = store.load_plan(run_id)
    study = store.load_report(run_id)
    hashes = store.artifact_hashes(run_id)
    final = AccountBPrefixFinalReport.model_validate_json(
        (store.plan_directory(run_id) / "prefix-final-report.json").read_text(encoding="utf-8")
    )
    animation = store.load_json(run_id, "prefix-animation-plan.json")
    validate_account_b_prefix_report(final)
    expected = {
        "run_id": run_id,
        "calendar_profile": "account-b",
        "calendar_name": "Calendar Animation Lab B",
        "frame_start": 0,
        "frame_count": 23,
        "output_fps": 3.0,
        "target_grid_width": 126,
        "target_grid_height": 72,
        "palette_preset": "cayde-final",
        "event_compression": "synchronized-horizontal-bands",
        "subcolumn_order_strategy": "zero-width",
    }
    for key, value in expected.items():
        if animation.get(key) != value:
            raise CalendarAnimError(f"Locked Account-B prefix invariant changed: {key}")
    if plan.parent_chunk_size != 100 or len(plan.parents) != final.recurring_parents:
        raise CalendarAnimError("Prefix chunk/parent count differs from approved artifacts")
    if any(
        parent.private_metadata.get("segment") != "prefix"
        or parent.private_metadata.get("human_frames") != "1-23"
        or parent.private_metadata.get("calendar_profile") != "account-b"
        for parent in plan.parents
    ):
        raise CalendarAnimError("Prefix parent metadata namespace is incomplete")
    expected_keys = _load_expected_prefix_occurrence_keys(animation)
    report = build_dry_run_report(
        run_id,
        plan,
        study,
        hashes,
        final.input_sha256,
        expected_occurrence_keys=expected_keys,
    )
    _validate_parent_serialization(plan)
    if not report.expansion_equality or not report.unique_parent_ids:
        raise CalendarAnimError("Prefix recurrence expansion equivalence gate failed")
    return plan, report, hashes, animation, final


def _load_expected_prefix_occurrence_keys(animation: dict[str, object]) -> set[str]:
    raw_frames = animation.get("frames")
    if not isinstance(raw_frames, list):
        raise CalendarAnimError("Prefix animation frame list is invalid")
    indices = [int(item["frame_index"]) for item in raw_frames if isinstance(item, dict)]
    if indices != list(range(23)):
        raise CalendarAnimError("Prefix animation must contain exactly frame indices 0-22")
    source_store = AnimationRunStore()
    source_plan = source_store.load_plan("cayde-final-126x72-3fps-36s-01")
    keys: set[str] = set()
    total = 0
    for frame_index in indices:
        frame_plan = source_store.load_frame_plan(source_plan, frame_index)
        for event in frame_plan.events:
            actual_index = event.frame_index if event.frame_index is not None else frame_index
            key = f"f{actual_index:04d}:{deterministic_event_id(event)}"
            total += 1
            keys.add(key)
    expected_total = animation.get("total_events")
    if not isinstance(expected_total, int) or total != expected_total or len(keys) != total:
        raise CalendarAnimError("Original prefix standalone occurrence set changed or duplicated")
    return keys


def prepare_account_b_prefix_upload_command(
    run_id: Annotated[str, typer.Option("--run-id")] = ACCOUNT_B_PREFIX_RUN_ID,
    input_file: Annotated[Path, typer.Option("--input")] = Path("input.mp4"),
    plan_root: Annotated[Path, typer.Option("--plan-root")] = Path("output/account-b-prefix-plans"),
    state_root: Annotated[Path, typer.Option("--state-root")] = Path(
        "output/account-b-prefix-runs"
    ),
) -> None:
    """Validate and checkpoint the Account-B prefix locally; make no API calls."""
    try:
        store = _prefix_store(plan_root, state_root)
        plan, report, hashes, _animation, _final = _load_and_validate_prefix(
            store, run_id, input_file
        )
        state = store.initialize_state(run_id, plan, hashes, INITIAL_WRITE_INTERVAL_SECONDS)
        dry_path = store.save_dry_run(report)
        store.save_performance(performance_from_state(state, 0))
    except (CalendarAnimError, OSError, ValueError) as error:
        _fail(error)
    _print_local_summary(report)
    typer.echo(f"State: {store.state_path(run_id)}")
    typer.echo(f"Dry-run report: {dry_path}")
    typer.echo("Google Calendar reads: NO")
    typer.echo("Google Calendar writes: NO")


def upload_account_b_prefix_recurrence_command(
    run_id: Annotated[str, typer.Option("--run-id")] = ACCOUNT_B_PREFIX_RUN_ID,
    profile_name: Annotated[str, typer.Option("--profile")] = "account-b",
    input_file: Annotated[Path, typer.Option("--input")] = Path("input.mp4"),
    plan_root: Annotated[Path, typer.Option("--plan-root")] = Path("output/account-b-prefix-plans"),
    state_root: Annotated[Path, typer.Option("--state-root")] = Path(
        "output/account-b-prefix-runs"
    ),
    resume: Annotated[bool, typer.Option("--resume")] = False,
    execute: Annotated[bool, typer.Option("--execute")] = False,
) -> None:
    """Upload only Account-B frames 1-23 using the validated resumable uploader."""
    if profile_name != "account-b":
        _fail(CalendarAnimError("Prefix recurrence upload is restricted to account-b"))
    try:
        store = _prefix_store(plan_root, state_root)
        plan, report, hashes, animation, final = _load_and_validate_prefix(
            store, run_id, input_file
        )
        state = store.initialize_state(run_id, plan, hashes, INITIAL_WRITE_INTERVAL_SECONDS)
        store.save_dry_run(report)
    except (CalendarAnimError, OSError, ValueError) as error:
        _fail(error)
    _print_local_summary(report)
    typer.echo("PROFILE: account-b")
    typer.echo("CALENDAR: Calendar Animation Lab B")
    typer.echo("FRAMES: 1-23")
    typer.echo("WRITE INTERVAL FLOOR: 1.0s")
    typer.echo("RESUME: enabled" if resume else "RESUME: disabled")
    typer.echo(f"EXECUTION: {'REAL' if execute else 'DRY RUN'}")
    if not execute:
        typer.echo("No authentication or Calendar API call was performed.")
        return
    if not resume:
        _fail(CalendarAnimError("Real prefix upload requires --resume"))
    try:
        profiles = CalendarProfileService(
            CalendarProfileStore(), gateway_factory=GoogleRecurrenceUploadGateway
        )
        profile, gateway = profiles.gateway("account-b")
        if not isinstance(gateway, GoogleRecurrenceUploadGateway):
            raise CalendarAnimError("Account-B gateway does not support recurrence upload")
        if profile.calendar_name != final.calendar_name or not profile.calendar_id:
            raise CalendarAnimError("Account-B profile calendar differs from prefix plan")
        calendar = gateway.get_calendar(profile.calendar_id)
        if calendar is None or calendar.access_role != "owner":
            raise CalendarAnimError("Prefix upload requires owner access to Account-B Calendar")
        if calendar.name != final.calendar_name or calendar.timezone != plan.timezone:
            raise CalendarAnimError("Account-B Calendar name/timezone differs from prefix plan")
        if state.calendar_id not in {None, calendar.id}:
            raise CalendarAnimError("Prefix checkpoint belongs to another Calendar")
        if state.completed_count == 0:
            _preflight_prefix_window(gateway, calendar.id, run_id, animation)
        typer.echo(f"ACCOUNT: {profile.authenticated_google_account}")
        typer.echo(f"CALENDAR ID: {calendar.id}")
        typer.echo("PREFIX REMOTE WINDOW: CLEAN/OWN-RUN ONLY")
        typer.confirm("Upload only Account-B prefix frames 1-23?", default=False, abort=True)
        gateway.restore_write_pacing(state.write_pacing)

        def progress(current):  # type: ignore[no-untyped-def]
            performance = performance_from_state(current, 0)
            typer.echo(
                f"Parents: {performance.parents_completed}/"
                f"{performance.total_parents_planned} | "
                f"interval={performance.current_write_interval_seconds:.2f}s | "
                f"rateLimitExceeded={performance.rate_limit_exceeded_count} | "
                f"quotaExceeded={performance.quota_exceeded_count}"
            )

        def quota_wait(wait, remaining):  # type: ignore[no-untyped-def]
            typer.secho("Calendar usage quota reached; checkpoint saved.", fg="yellow")
            typer.echo(f"Next retry: {wait.next_retry_at.astimezone()}")
            typer.echo(f"Sleep: {remaining:.0f}s; Ctrl+C preserves the checkpoint.")

        service = RecurrenceUploadService(
            gateway,
            store,
            quota_policy=QuotaWaitPolicy(
                cooldown_seconds=(900, 1800, 3600, 7200, 14400),
                jitter_seconds=60,
                max_auto_wait_seconds=48 * 3600,
                conservative_recovery_interval_seconds=1.5,
            ),
            progress=progress,
            quota_wait=quota_wait,
        )
        state = service.upload(plan, state, calendar.id)
    except KeyboardInterrupt:
        typer.secho("Interrupted safely; atomic checkpoint preserved.", fg="yellow")
        raise typer.Exit(code=130) from None
    except (CalendarAnimError, HttpError, OSError, ValueError) as error:
        _fail(error)
    typer.echo(f"Completed parents: {state.completed_count}/{len(state.parents)}")
    typer.echo(f"Performance: {store.performance_json_path(run_id)}")


def _preflight_prefix_window(
    gateway: GoogleRecurrenceUploadGateway,
    calendar_id: str,
    run_id: str,
    animation: dict[str, object],
) -> None:
    frames = animation.get("frames")
    if not isinstance(frames, list) or not frames:
        raise CalendarAnimError("Prefix animation plan has no frames")
    weeks = [
        date.fromisoformat(str(item["week_start"])) for item in frames if isinstance(item, dict)
    ]
    zone = ZoneInfo("America/Sao_Paulo")
    start = datetime.combine(min(weeks), time.min, zone)
    end = datetime.combine(max(weeks) + timedelta(days=7), time.min, zone)
    events = gateway.list_window(calendar_id, start, end)
    unexpected = []
    for event in events:
        metadata = event.get("extendedProperties", {}).get("private", {})
        if not isinstance(metadata, dict) or any(
            metadata.get(key) != value
            for key, value in {
                "generated_by": "calendar-anim",
                "run_id": run_id,
                "calendar_profile": "account-b",
                "segment": "prefix",
            }.items()
        ):
            unexpected.append(event)
    if unexpected:
        raise CalendarAnimError(
            f"Prefix preflight found {len(unexpected)} unexpected event(s); no writes started"
        )


def register_prefix_recurrence_upload_commands(app: typer.Typer) -> None:
    app.command("prepare-account-b-prefix-upload")(prepare_account_b_prefix_upload_command)
    app.command("upload-account-b-prefix-recurrence")(upload_account_b_prefix_recurrence_command)
