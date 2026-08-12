from pathlib import Path
from typing import Annotated, Never

import typer
from googleapiclient.errors import HttpError

from calendar_anim.calendar.hybrid_capture.planner import parse_human_frames
from calendar_anim.calendar.multi_frame.artifacts import AnimationRunStore
from calendar_anim.calendar.profiles.service import CalendarProfileService
from calendar_anim.calendar.profiles.store import CalendarProfileStore
from calendar_anim.calendar.recurrence_compaction.hybrid import FINAL_HYBRID_RUN_ID
from calendar_anim.calendar.recurrence_upload.artifacts import RecurrenceUploadStore
from calendar_anim.calendar.remote_recurrence_audit.artifacts import (
    RemoteRecurrenceAuditStore,
)
from calendar_anim.calendar.remote_recurrence_audit.gateway import (
    GoogleRemoteRecurrenceAuditGateway,
)
from calendar_anim.calendar.remote_recurrence_audit.service import (
    RemoteRecurrenceAuditService,
)
from calendar_anim.exceptions import CalendarAnimError

DEFAULT_AUDIT_FRAMES = "24,40,60,100"


def _fail(error: Exception) -> Never:
    typer.secho(f"Error: {error}", fg=typer.colors.RED, err=True)
    raise typer.Exit(code=1)


def audit_hybrid_recurrence_remote_command(
    run_id: Annotated[str, typer.Option("--run-id")] = FINAL_HYBRID_RUN_ID,
    profile_name: Annotated[str, typer.Option("--profile")] = "account-b",
    frames: Annotated[str, typer.Option("--frames")] = DEFAULT_AUDIT_FRAMES,
    plan_root: Annotated[Path, typer.Option("--plan-root")] = Path("output/hybrid-plans"),
    state_root: Annotated[Path, typer.Option("--state-root")] = Path("output/hybrid-runs"),
) -> None:
    """Compare locked expected occurrences with Google API expansion, read-only."""

    if run_id != FINAL_HYBRID_RUN_ID:
        _fail(CalendarAnimError("Remote audit is locked to the final hybrid run"))
    if profile_name != "account-b":
        _fail(CalendarAnimError("Remote audit is restricted to account-b"))
    try:
        selected = parse_human_frames(frames)
        if any(value < 24 for value in selected):
            raise CalendarAnimError("Account-B audit frames must be in the range 24-108")
        upload_store = RecurrenceUploadStore(plan_root, state_root)
        recurrence_plan = upload_store.load_plan(run_id)
        hashes = upload_store.artifact_hashes(run_id)
        state = upload_store.load_state(run_id)
        upload_store.validate_state(state, recurrence_plan, hashes)
        if state.completed_count != len(recurrence_plan.parents):
            raise CalendarAnimError("Account-B recurrence bulk is not fully completed")
        animation = upload_store.load_json(run_id, "account-b-animation-plan.json")
        hybrid = upload_store.load_json(run_id, "hybrid-final-plan.json")
        source_run_id = str(hybrid.get("source_run_id") or "")
        source_store = AnimationRunStore()
        source_plan = source_store.load_plan(source_run_id)
        profiles = CalendarProfileService(
            CalendarProfileStore(), gateway_factory=GoogleRemoteRecurrenceAuditGateway
        )
        profile, base_gateway = profiles.gateway(profile_name)
        if not isinstance(base_gateway, GoogleRemoteRecurrenceAuditGateway):
            raise CalendarAnimError("Profile gateway is not the read-only audit gateway")
        if not profile.calendar_id or profile.calendar_name != "Calendar Animation Lab B":
            raise CalendarAnimError("Account-B profile does not target Calendar Animation Lab B")
        calendar = base_gateway.get_calendar(profile.calendar_id)
        if calendar is None or calendar.name != profile.calendar_name:
            raise CalendarAnimError("Calendar Animation Lab B was not found")
        if calendar.timezone != recurrence_plan.timezone:
            raise CalendarAnimError("Remote Calendar timezone differs from recurrence plan")
        typer.echo("REMOTE RECURRENCE AUDIT")
        typer.echo(f"Run: {run_id}")
        typer.echo(f"Profile: {profile_name}")
        typer.echo(f"Calendar: {calendar.name}")
        typer.echo(f"Frames: {', '.join(str(value) for value in selected)}")
        typer.echo("API operations: events.list + events.get only")
        typer.echo("Google Calendar writes: NO")
        report = RemoteRecurrenceAuditService(base_gateway, source_store).audit(
            run_id=run_id,
            profile=profile_name,
            calendar_name=calendar.name,
            calendar_id=calendar.id,
            recurrence_plan=recurrence_plan,
            animation_artifact=animation,
            source_plan=source_plan,
            human_frames=selected,
        )
        artifact_store = RemoteRecurrenceAuditStore(state_root)
        json_path, text_path = artifact_store.save(report)
    except (CalendarAnimError, HttpError, OSError, ValueError) as error:
        _fail(error)
    for frame in report.frames:
        typer.echo(
            f"Frame {frame.human_frame}: expected={frame.expected_occurrences}, "
            f"remote={frame.google_expanded_occurrences}, exact={frame.exact_matches}, "
            f"missing={frame.missing}, extra={frame.extra}, duplicates={frame.duplicates}"
        )
    typer.echo(f"Root cause: {report.root_cause_category} — {report.root_cause}")
    typer.echo(f"JSON: {json_path}")
    typer.echo(f"Text: {text_path}")
    typer.echo("Google Calendar reads: YES")
    typer.echo("Google Calendar writes: NO")


def register_remote_recurrence_audit_commands(app: typer.Typer) -> None:
    app.command("audit-hybrid-recurrence-remote")(audit_hybrid_recurrence_remote_command)
