from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Annotated, Never
from zoneinfo import ZoneInfo

import typer

from calendar_anim.calendar.cayde_216.artifacts import Cayde216Store, write_atomic
from calendar_anim.calendar.cayde_216.capture import (
    capture_cayde_216_preview_command,
    capture_final_cayde_216_command,
)
from calendar_anim.calendar.cayde_216.models import (
    Cayde216RemotePreflight,
    Cayde216WindowSearchReport,
)
from calendar_anim.calendar.cayde_216.planner import (
    FIRST_WEEK,
    FRAME_COUNT,
    OLD_LAST_WEEK,
    RUN_ID,
    SOURCE_RUN_ID,
    build_cayde_216_plan,
    protected_hashes,
)
from calendar_anim.calendar.cayde_216.toolbar_composition import (
    compose_final_cayde_216_calendar_toolbar_command,
    mux_final_cayde_216_calendar_toolbar_audio_command,
    preview_cayde_216_calendar_toolbar_command,
    recompose_final_cayde_216_calendar_toolbar_command,
)
from calendar_anim.calendar.cayde_216.upload import upload_cayde_216_recurrence_command
from calendar_anim.calendar.cayde_216.window_search import find_clean_windows
from calendar_anim.calendar.profiles.service import CalendarProfileService
from calendar_anim.calendar.profiles.store import CalendarProfileStore
from calendar_anim.exceptions import CalendarAnimError


def _fail(error: Exception) -> Never:
    typer.secho(f"Error: {error}", fg=typer.colors.RED, err=True)
    raise typer.Exit(code=1)


def prepare_cayde_216_command(
    run_id: Annotated[str, typer.Option("--run-id")] = RUN_ID,
    input_file: Annotated[Path, typer.Option("--input")] = Path("input.mp4"),
    output_root: Annotated[Path, typer.Option("--output-root")] = Path("output/216-plans"),
) -> None:
    """Build the isolated 216-frame/6-FPS plans and sizing reports locally."""

    try:
        if run_id != RUN_ID:
            raise CalendarAnimError(f"216-frame preparation requires locked run ID {RUN_ID}")
        report, artifacts = build_cayde_216_plan(
            store=Cayde216Store(output_root), input_path=input_file
        )
    except (CalendarAnimError, OSError, RuntimeError, ValueError) as error:
        _fail(error)
    typer.echo("CAYDE FINAL 216F / 6FPS PLAN")
    typer.echo("============================")
    typer.echo(f"Frames: {report.frame_count} (indices 0-{report.frame_indices[-1]})")
    typer.echo(f"FPS/duration: {report.fps:g} / {report.duration_seconds:.1f}s")
    typer.echo(f"Weeks: {report.first_week} -> {report.last_week}")
    typer.echo(f"Overlap with old weeks: {report.old_week_overlap}")
    typer.echo(f"Logical occurrences: {report.logical_occurrences}")
    typer.echo(f"Parents chunk100: {report.recurring_parents}")
    typer.echo(f"Reduction: {report.reduction_percent:.3f}%")
    typer.echo(f"Expansion equality: {'YES' if report.expansion_exact else 'NO'}")
    typer.echo(f"Existing parent ID collisions: {report.parent_id_collisions_with_existing_b}")
    typer.echo(f"Payload max: {report.payload.maximum_bytes} bytes")
    for artifact in artifacts:
        typer.echo(f"Artifact: {artifact}")
    typer.echo("OLD 108 VERSION TOUCHED: NO")
    typer.echo("Google Calendar reads: NO")
    typer.echo("Google Calendar writes: NO")


def search_cayde_216_windows_command(
    run_id: Annotated[str, typer.Option("--run-id")] = SOURCE_RUN_ID,
    profile: Annotated[str, typer.Option("--profile")] = "account-b",
    execute: Annotated[bool, typer.Option("--execute")] = False,
) -> None:
    """Find two disjoint clean 216-week ranges through one read-only API scan."""

    if run_id != SOURCE_RUN_ID or profile != "account-b":
        _fail(CalendarAnimError("216-frame window search is locked to its run and account-b"))
    search_start = OLD_LAST_WEEK + timedelta(weeks=1)
    search_end = search_start + timedelta(weeks=1040)
    typer.echo("CAYDE 216 CLEAN WINDOW SEARCH")
    typer.echo(f"Profile: {profile}")
    typer.echo(f"Query: {search_start} -> {search_end} (exclusive)")
    typer.echo(f"Execution: {'READ-ONLY GOOGLE API' if execute else 'DRY RUN'}")
    if not execute:
        typer.echo("No Google API call was made.")
        typer.echo("Google Calendar writes: NO")
        return
    store = Cayde216Store()
    try:
        before = protected_hashes()
        account, gateway = CalendarProfileService(CalendarProfileStore()).gateway(profile)
        if account.calendar_id is None:
            raise CalendarAnimError("Account B has no configured Calendar ID")
        calendar = gateway.get_calendar(account.calendar_id)
        if calendar is None:
            raise CalendarAnimError("Configured Account-B calendar is not remotely accessible")
        zone = ZoneInfo("America/Sao_Paulo")
        events = gateway.list_events_in_range(
            account.calendar_id,
            datetime.combine(search_start, time.min, zone),
            datetime.combine(search_end, time.min, zone),
        )
        candidates = find_clean_windows(
            events,
            search_start=search_start,
            search_end_exclusive=search_end,
            timezone="America/Sao_Paulo",
        )
        after = protected_hashes()
        identity_ok = (
            account.profile_name == "account-b"
            and account.calendar_name == "Calendar Animation Lab B"
            and account.timezone == "America/Sao_Paulo"
            and calendar.id == account.calendar_id
            and calendar.name == "Calendar Animation Lab B"
            and calendar.timezone == "America/Sao_Paulo"
            and calendar.access_role == "owner"
        )
        report = Cayde216WindowSearchReport(
            run_id=run_id,
            profile=profile,
            authenticated_account=account.authenticated_google_account or "unknown",
            calendar_id=account.calendar_id,
            calendar_name=calendar.name,
            timezone=calendar.timezone,
            query_start=search_start,
            query_end_exclusive=search_end,
            expanded_events_seen=len(events),
            candidates=candidates,
            old_artifacts_unchanged=before == after,
            result=("PASS" if identity_ok and before == after and len(candidates) >= 2 else "STOP"),
        )
        artifacts = store.save_window_search(report)
        if report.result != "PASS":
            raise CalendarAnimError(
                "Clean-window search STOP: identity, protected artifacts, or range search failed"
            )
        recommended = report.candidates[0]
        preparation_status = {
            "schema_version": "1.0",
            "run_id": run_id,
            "status": "BLOCKED_PENDING_PALETTE_APPROVAL_AND_REPLAN",
            "bulk_upload_ready": False,
            "selected_palette": None,
            "palette_approval_required": True,
            "recommended_clean_window": recommended.model_dump(mode="json"),
            "current_local_plan_first_week": search_start.isoformat(),
            "current_local_plan_is_remote_clean": False,
            "replan_required_after_palette_approval": True,
            "do_not_upload_current_plan": True,
            "google_calendar_reads": True,
            "google_calendar_writes": False,
        }
        artifacts.append(
            store.save_json_report(
                store.run_directory(run_id) / "preparation-status.json", preparation_status
            )
        )
        artifacts.append(
            write_atomic(
                store.run_directory(run_id) / "preparation-status.txt",
                "\n".join(
                    [
                        "CAYDE 216 PREPARATION STATUS",
                        "============================",
                        "",
                        "Bulk upload ready: NO",
                        "Selected palette: NONE",
                        "Palette visual approval required: YES",
                        f"Recommended clean frame 1: {recommended.first_week}",
                        f"Recommended clean frame 216: {recommended.last_week}",
                        "Replan after palette approval: YES",
                        "Do not upload current local plan: YES",
                        "Google Calendar writes: NO",
                        "",
                    ]
                ),
            )
        )
    except (CalendarAnimError, OSError, RuntimeError, ValueError) as error:
        _fail(error)
    typer.echo(f"Expanded events seen: {report.expanded_events_seen}")
    for candidate in report.candidates:
        typer.echo(
            f"Option {candidate.rank}: {candidate.first_week} -> {candidate.last_week} "
            "(216 clean weeks)"
        )
    for artifact in artifacts:
        typer.echo(f"Artifact: {artifact}")
    typer.echo("Old resources touched: NO")
    typer.echo("Google Calendar reads: YES")
    typer.echo("Google Calendar writes: NO")


def preflight_cayde_216_command(
    run_id: Annotated[str, typer.Option("--run-id")] = RUN_ID,
    profile: Annotated[str, typer.Option("--profile")] = "account-b",
    execute: Annotated[bool, typer.Option("--execute")] = False,
) -> None:
    """Read-only Account-B identity and empty-range preflight for the 216-frame run."""

    store = Cayde216Store()
    if run_id != RUN_ID or profile != "account-b":
        _fail(CalendarAnimError("216-frame preflight is locked to its run ID and account-b"))
    end_exclusive = FIRST_WEEK + timedelta(weeks=FRAME_COUNT)
    typer.echo("CAYDE 216 REMOTE PREFLIGHT")
    typer.echo(f"Profile: {profile}")
    typer.echo(f"Range: {FIRST_WEEK} -> {end_exclusive} (exclusive)")
    typer.echo(f"Execution: {'READ-ONLY GOOGLE API' if execute else 'DRY RUN'}")
    if not execute:
        typer.echo("No Google API call was made.")
        typer.echo("Google Calendar writes: NO")
        return
    try:
        before = protected_hashes()
        account, gateway = CalendarProfileService(CalendarProfileStore()).gateway(profile)
        if account.calendar_id is None:
            raise CalendarAnimError("Account B has no configured Calendar ID")
        calendar = gateway.get_calendar(account.calendar_id)
        if calendar is None:
            raise CalendarAnimError("Configured Account-B calendar is not remotely accessible")
        zone = ZoneInfo("America/Sao_Paulo")
        event_ids = gateway.list_event_ids_in_range(
            account.calendar_id,
            datetime.combine(FIRST_WEEK, time.min, tzinfo=zone),
            datetime.combine(end_exclusive, time.min, tzinfo=zone),
        )
        after = protected_hashes()
        clean = not event_ids
        identity_ok = (
            account.profile_name == "account-b"
            and account.calendar_name == "Calendar Animation Lab B"
            and account.timezone == "America/Sao_Paulo"
            and calendar.id == account.calendar_id
            and calendar.name == "Calendar Animation Lab B"
            and calendar.timezone == "America/Sao_Paulo"
            and calendar.access_role == "owner"
        )
        report = Cayde216RemotePreflight(
            run_id=run_id,
            profile=profile,
            authenticated_account=account.authenticated_google_account or "unknown",
            expected_calendar_id=account.calendar_id,
            remote_calendar_id=calendar.id,
            calendar_name=calendar.name,
            access_role=calendar.access_role or "unknown",
            timezone=calendar.timezone,
            range_start=FIRST_WEEK,
            range_end_exclusive=end_exclusive,
            unexpected_event_count=len(event_ids),
            unexpected_event_ids=event_ids[:100],
            new_range_clean=clean,
            old_artifacts_unchanged=before == after,
            result="PASS" if clean and identity_ok and before == after else "STOP",
        )
        store.save_preflight(report)
        if report.result != "PASS":
            raise CalendarAnimError(
                "216-frame remote preflight STOP: identity, protected artifacts, "
                "or new range failed"
            )
    except (CalendarAnimError, OSError, RuntimeError, ValueError) as error:
        _fail(error)
    typer.echo(f"Account: {report.authenticated_account}")
    typer.echo(f"Calendar: {report.calendar_name} ({report.access_role})")
    typer.echo(f"Timezone: {report.timezone}")
    typer.echo(f"New range clean: {'YES' if report.new_range_clean else 'NO'}")
    typer.echo(f"Unexpected events: {report.unexpected_event_count}")
    typer.echo(f"Old artifacts unchanged: {'YES' if report.old_artifacts_unchanged else 'NO'}")
    typer.echo(f"Result: {report.result}")
    typer.echo(f"Report: {store.preflight_path(run_id)}")
    typer.echo("Google Calendar writes: NO")


def register_cayde_216_commands(app: typer.Typer) -> None:
    app.command("prepare-cayde-216")(prepare_cayde_216_command)
    app.command("preflight-cayde-216")(preflight_cayde_216_command)
    app.command("search-cayde-216-windows")(search_cayde_216_windows_command)
    app.command("upload-cayde-216-recurrence")(upload_cayde_216_recurrence_command)
    app.command("capture-cayde-216-preview")(capture_cayde_216_preview_command)
    app.command("capture-final-cayde-216")(capture_final_cayde_216_command)
    app.command("preview-cayde-216-calendar-toolbar")(preview_cayde_216_calendar_toolbar_command)
    app.command("recompose-final-cayde-216-calendar-toolbar")(
        recompose_final_cayde_216_calendar_toolbar_command
    )
    app.command("compose-final-cayde-216-calendar-toolbar")(
        compose_final_cayde_216_calendar_toolbar_command
    )
    app.command("mux-final-cayde-216-calendar-toolbar-audio")(
        mux_final_cayde_216_calendar_toolbar_audio_command
    )
