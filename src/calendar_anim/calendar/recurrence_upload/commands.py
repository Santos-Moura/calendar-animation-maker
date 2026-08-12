import re
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
from calendar_anim.calendar.recurrence_compaction.hybrid import (
    FINAL_HYBRID_RUN_ID,
    FINAL_INPUT_SHA256,
)
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
INITIAL_WRITE_INTERVAL_SECONDS = 1.0


def _fail(error: Exception) -> Never:
    typer.secho(f"Error: {error}", fg=typer.colors.RED, err=True)
    raise typer.Exit(code=1)


def _load_and_validate(store: RecurrenceUploadStore, run_id: str, input_file: Path):  # type: ignore[no-untyped-def]
    if run_id != FINAL_HYBRID_RUN_ID:
        raise CalendarAnimError("Only the approved final hybrid run ID may use this uploader")
    hashes = store.artifact_hashes(run_id)
    plan = store.load_plan(run_id)
    study = store.load_report(run_id)
    animation = store.load_json(run_id, "account-b-animation-plan.json")
    hybrid = store.load_json(run_id, "hybrid-final-plan.json")
    source_hash = file_sha256(input_file)
    if source_hash != FINAL_INPUT_SHA256:
        raise CalendarAnimError("input.mp4 SHA-256 differs from the approved final source")
    expected_animation = {
        "run_id": run_id,
        "calendar_profile": "account-b",
        "calendar_name": "Calendar Animation Lab B",
        "frame_start": 23,
        "frame_count": 85,
        "total_events": 214596,
        "output_fps": 3.0,
        "target_grid_width": 126,
        "target_grid_height": 72,
        "palette_preset": "cayde-final",
        "event_compression": "synchronized-horizontal-bands",
        "subcolumn_order_strategy": "zero-width",
    }
    for key, value in expected_animation.items():
        if animation.get(key) != value:
            raise CalendarAnimError(f"Approved Account-B animation invariant changed: {key}")
    if hybrid.get("ordering_result") != "PASS":
        raise CalendarAnimError("Recurrence ordering gate is not PASS")
    if hybrid.get("input_sha256") != FINAL_INPUT_SHA256:
        raise CalendarAnimError("Hybrid plan source hash differs from approved input")
    if plan.parent_chunk_size != 100 or len(plan.parents) != 32021:
        raise CalendarAnimError("Approved chunk100/32021-parent recurrence plan changed")
    expected_keys = _load_expected_account_b_occurrence_keys(hybrid, animation)
    report = build_dry_run_report(
        run_id,
        plan,
        study,
        hashes,
        source_hash,
        expected_occurrence_keys=expected_keys,
    )
    _validate_parent_serialization(plan)
    if not report.expansion_equality or not report.unique_parent_ids:
        raise CalendarAnimError("Recurrence expansion equivalence gate failed")
    return plan, report, hashes, animation


def _load_expected_account_b_occurrence_keys(
    hybrid: dict[str, object], animation: dict[str, object]
) -> set[str]:
    source_run_id = str(hybrid.get("source_run_id") or "")
    if source_run_id != "cayde-final-126x72-3fps-36s-01":
        raise CalendarAnimError("Hybrid source run differs from the approved final run")
    raw_frames = animation.get("frames")
    if not isinstance(raw_frames, list):
        raise CalendarAnimError("Account-B animation frame list is invalid")
    expected_indices = list(range(23, 108))
    frame_indices = [int(item["frame_index"]) for item in raw_frames if isinstance(item, dict)]
    if frame_indices != expected_indices:
        raise CalendarAnimError("Account-B frame sequence differs from indices 23-107")
    source_store = AnimationRunStore()
    source_plan = source_store.load_plan(source_run_id)
    occurrence_keys: set[str] = set()
    total = 0
    for frame_index in expected_indices:
        frame_plan = source_store.load_frame_plan(source_plan, frame_index)
        for event in frame_plan.events:
            actual_index = event.frame_index if event.frame_index is not None else frame_index
            key = f"f{actual_index:04d}:{deterministic_event_id(event)}"
            total += 1
            occurrence_keys.add(key)
    if total != 214596 or len(occurrence_keys) != total:
        raise CalendarAnimError(
            "Original Account-B standalone occurrence set is incomplete or duplicated"
        )
    return occurrence_keys


def _validate_parent_serialization(plan: RecurrenceMigrationPlan) -> None:
    gateway = GoogleRecurrenceUploadGateway(None)
    seen: set[str] = set()
    for parent in plan.parents:
        if parent.parent_id in seen:
            raise CalendarAnimError(f"Duplicate deterministic parent ID: {parent.parent_id}")
        seen.add(parent.parent_id)
        body = gateway.parent_body(parent)
        if (
            body["summary"] != parent.signature.summary
            or body.get("colorId") != parent.signature.color_id
        ):
            raise CalendarAnimError(
                f"Parent visual signature serialization failed: {parent.parent_id}"
            )
        if parent.occurrence_count > 100:
            raise CalendarAnimError(f"Parent exceeds chunk size 100: {parent.parent_id}")
        for line in parent.recurrence:
            match = RDATE_PATTERN.fullmatch(line)
            if match is None or match.group(1) != plan.timezone:
                raise CalendarAnimError(f"Invalid RDATE serialization: {parent.parent_id}")
            for value in match.group(2).split(","):
                datetime.strptime(value, "%Y%m%dT%H%M%S")


def prepare_hybrid_recurrence_upload_command(
    run_id: Annotated[str, typer.Option("--run-id")] = FINAL_HYBRID_RUN_ID,
    input_file: Annotated[Path, typer.Option("--input")] = Path("input.mp4"),
    plan_root: Annotated[Path, typer.Option("--plan-root")] = Path("output/hybrid-plans"),
    state_root: Annotated[Path, typer.Option("--state-root")] = Path("output/hybrid-runs"),
) -> None:
    """Validate every parent locally and initialize the separate Account-B checkpoint."""
    try:
        store = RecurrenceUploadStore(plan_root, state_root)
        plan, report, hashes, _animation = _load_and_validate(store, run_id, input_file)
        state = store.initialize_state(run_id, plan, hashes, INITIAL_WRITE_INTERVAL_SECONDS)
        dry_path = store.save_dry_run(report)
        performance = performance_from_state(state, 0)
        store.save_performance(performance)
    except (CalendarAnimError, OSError, ValueError) as error:
        _fail(error)
    _print_local_summary(report)
    typer.echo(f"State: {store.state_path(run_id)}")
    typer.echo(f"Dry-run report: {dry_path}")
    typer.echo("Google Calendar reads: NO")
    typer.echo("Google Calendar writes: NO")


def upload_hybrid_recurrence_command(
    run_id: Annotated[str, typer.Option("--run-id")] = FINAL_HYBRID_RUN_ID,
    profile_name: Annotated[str, typer.Option("--profile")] = "account-b",
    input_file: Annotated[Path, typer.Option("--input")] = Path("input.mp4"),
    plan_root: Annotated[Path, typer.Option("--plan-root")] = Path("output/hybrid-plans"),
    state_root: Annotated[Path, typer.Option("--state-root")] = Path("output/hybrid-runs"),
    resume: Annotated[bool, typer.Option("--resume")] = False,
    execute: Annotated[bool, typer.Option("--execute")] = False,
) -> None:
    """Upload the approved Account-B recurring parents with unattended recovery."""
    if profile_name != "account-b":
        _fail(CalendarAnimError("Final hybrid recurrence bulk is restricted to account-b"))
    try:
        store = RecurrenceUploadStore(plan_root, state_root)
        plan, report, hashes, animation = _load_and_validate(store, run_id, input_file)
        state = store.initialize_state(run_id, plan, hashes, INITIAL_WRITE_INTERVAL_SECONDS)
        store.save_dry_run(report)
    except (CalendarAnimError, OSError, ValueError) as error:
        _fail(error)
    _print_local_summary(report)
    typer.echo(f"PROFILE: {profile_name}")
    typer.echo("CALENDAR: Calendar Animation Lab B")
    typer.echo(f"RUN: {run_id}")
    typer.echo("FRAMES: 24-108")
    typer.echo("RESUME: enabled" if resume else "RESUME: disabled")
    typer.echo("UNATTENDED QUOTA RECOVERY: enabled")
    typer.echo(f"EXECUTION: {'REAL' if execute else 'DRY RUN'}")
    if not execute:
        typer.echo("No authentication or Calendar API call was performed.")
        return
    if not resume:
        _fail(CalendarAnimError("Real bulk requires --resume for checkpoint acknowledgement"))
    try:
        profiles = CalendarProfileService(
            CalendarProfileStore(), gateway_factory=GoogleRecurrenceUploadGateway
        )
        profile, base_gateway = profiles.gateway("account-b")
        if not isinstance(base_gateway, GoogleRecurrenceUploadGateway):
            raise CalendarAnimError("Account-B gateway does not support recurrence upload")
        if profile.calendar_name != "Calendar Animation Lab B" or not profile.calendar_id:
            raise CalendarAnimError("Account-B profile calendar differs from final plan")
        calendar = base_gateway.get_calendar(profile.calendar_id)
        if calendar is None:
            raise CalendarAnimError("Calendar Animation Lab B was not found")
        if calendar.name != "Calendar Animation Lab B" or calendar.access_role != "owner":
            raise CalendarAnimError("Account-B final calendar is not owner-controlled")
        if calendar.timezone != plan.timezone:
            raise CalendarAnimError("Account-B Calendar timezone differs from recurrence plan")
        if state.calendar_id not in {None, calendar.id}:
            raise CalendarAnimError("Checkpoint belongs to another Calendar")
        if state.completed_count == 0:
            _preflight_clean_final_window(base_gateway, calendar.id, run_id, animation)
        typer.echo(f"ACCOUNT: {profile.authenticated_google_account}")
        typer.echo(f"CALENDAR ID: {calendar.id}")
        typer.echo(f"OWNER: {calendar.access_role}")
        typer.echo(f"TIMEZONE: {calendar.timezone}")
        typer.echo("EXECUTION: REAL")
        typer.confirm("Start unattended Account-B recurrence bulk?", default=False, abort=True)
        base_gateway.restore_write_pacing(state.write_pacing)

        def progress(current):  # type: ignore[no-untyped-def]
            elapsed = (
                max(0.0, (datetime.now().astimezone() - current.started_at).total_seconds())
                if current.started_at
                else 0.0
            )
            perf = performance_from_state(current, elapsed)
            typer.echo(
                f"Parents: {perf.parents_completed}/{perf.total_parents_planned} | "
                f"Interval: {perf.current_write_interval_seconds:.2f}s | "
                f"Rate: {perf.parents_per_active_second:.3f}/s | "
                f"rateLimitExceeded: {perf.rate_limit_exceeded_count} | "
                f"quotaExceeded: {perf.quota_exceeded_count}"
            )
            if perf.active_upload_eta_seconds is not None:
                typer.echo(
                    f"Active ETA: {_duration(perf.active_upload_eta_seconds)} | "
                    f"Wall ETA: {_duration(perf.wall_clock_eta_seconds or 0)}"
                )

        def quota_wait(wait, remaining):  # type: ignore[no-untyped-def]
            typer.secho("Calendar usage quota reached; checkpoint saved.", fg="yellow")
            typer.echo(f"Next retry: {wait.next_retry_at.astimezone()}")
            typer.echo(f"Stage: {wait.stage_index + 1}; sleep: {remaining:.0f}s")
            typer.echo("Press Ctrl+C once for a clean checkpointed exit.")

        service = RecurrenceUploadService(
            base_gateway,
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


def cleanup_hybrid_recurrence_command(
    run_id: Annotated[str, typer.Option("--run-id")] = FINAL_HYBRID_RUN_ID,
    profile_name: Annotated[str, typer.Option("--profile")] = "account-b",
    execute: Annotated[bool, typer.Option("--execute")] = False,
) -> None:
    """Prepare exact Account-B bulk cleanup; deletion remains explicit and confirmed."""
    if profile_name != "account-b":
        _fail(CalendarAnimError("Hybrid cleanup is restricted to account-b"))
    typer.echo("PROFILE: account-b")
    typer.echo(f"RUN: {run_id}")
    typer.echo("FILTER: generated_by=calendar-anim + exact run_id + calendar_profile=account-b")
    typer.echo(f"EXECUTION: {'REAL DELETE' if execute else 'DRY RUN'}")
    if not execute:
        typer.echo("No authentication, Calendar read, or delete was performed.")
        return
    try:
        profiles = CalendarProfileService(
            CalendarProfileStore(), gateway_factory=GoogleRecurrenceUploadGateway
        )
        profile, gateway = profiles.gateway("account-b")
        if not isinstance(gateway, GoogleRecurrenceUploadGateway) or not profile.calendar_id:
            raise CalendarAnimError("Invalid Account-B cleanup gateway")
        if profile.calendar_name != "Calendar Animation Lab B":
            raise CalendarAnimError("Account-B cleanup profile calendar differs from final plan")
        calendar = gateway.get_calendar(profile.calendar_id)
        if (
            calendar is None
            or calendar.name != "Calendar Animation Lab B"
            or calendar.access_role != "owner"
        ):
            raise CalendarAnimError("Cleanup requires owner access to Calendar Animation Lab B")
        parents = gateway.find_bulk_parents(profile.calendar_id, run_id)
        ids = sorted(str(item["id"]) for item in parents)
        typer.echo(f"Matched recurring parents: {len(ids)}")
        typer.confirm("Delete only these exact Account-B recurring parents?", abort=True)
        result = gateway.delete_events(profile.calendar_id, ids)
    except (CalendarAnimError, HttpError, OSError, ValueError) as error:
        _fail(error)
    typer.echo(f"Deleted: {result.deleted_events}; failed: {result.failed_events}")


def _preflight_clean_final_window(
    gateway: GoogleRecurrenceUploadGateway,
    calendar_id: str,
    run_id: str,
    animation: dict[str, object],
) -> None:
    frames = animation.get("frames")
    if not isinstance(frames, list) or not frames:
        raise CalendarAnimError("Account-B animation plan has no frames")
    weeks = [
        date.fromisoformat(str(item["week_start"])) for item in frames if isinstance(item, dict)
    ]
    zone = ZoneInfo("America/Sao_Paulo")
    start = datetime.combine(min(weeks), time.min, zone)
    end = datetime.combine(max(weeks) + timedelta(days=7), time.min, zone)
    events = gateway.list_window(calendar_id, start, end)
    unrelated = []
    for event in events:
        metadata = event.get("extendedProperties", {}).get("private", {})
        if not isinstance(metadata, dict) or any(
            metadata.get(key) != value
            for key, value in {
                "generated_by": "calendar-anim",
                "run_id": run_id,
                "calendar_profile": "account-b",
            }.items()
        ):
            unrelated.append(event)
    if unrelated:
        raise CalendarAnimError(
            f"Preflight found {len(unrelated)} unrelated event(s) in final Account-B weeks"
        )


def _print_local_summary(report: RecurrenceDryRunReport) -> None:
    typer.echo(f"LOGICAL OCCURRENCES: {report.logical_occurrences}")
    typer.echo(f"RECURRING PARENTS: {report.parent_inserts}")
    typer.echo(f"CHUNK: {report.chunk_size}")
    typer.echo(f"REDUCTION: {report.reduction_percent:.3f}%")
    typer.echo(f"UNIQUE IDs: {'YES' if report.unique_parent_ids else 'NO'}")
    typer.echo(f"DUPLICATES: {report.duplicate_occurrences}")
    typer.echo(f"MISSING: {report.missing_occurrences}")
    typer.echo(f"EXTRA: {report.extra_occurrences}")
    typer.echo(f"EXPANSION EQUALITY: {'YES' if report.expansion_equality else 'NO'}")
    typer.echo(
        "PAYLOAD bytes: "
        f"min={report.payload.minimum_bytes}, mean={report.payload.mean_bytes:.1f}, "
        f"p95={report.payload.p95_bytes}, max={report.payload.maximum_bytes}"
    )
    for interval in (0.75, 1.0, 1.5, 2.0):
        typer.echo(f"ETA @{interval:.2f}s/write: {_duration(report.parent_inserts * interval)}")


def _duration(seconds: float) -> str:
    total = round(seconds)
    hours, remainder = divmod(total, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours}h {minutes:02d}m {seconds:02d}s"


def register_recurrence_upload_commands(app: typer.Typer) -> None:
    app.command("prepare-hybrid-recurrence-upload")(prepare_hybrid_recurrence_upload_command)
    app.command("upload-hybrid-recurrence")(upload_hybrid_recurrence_command)
    app.command("cleanup-hybrid-recurrence")(cleanup_hybrid_recurrence_command)
