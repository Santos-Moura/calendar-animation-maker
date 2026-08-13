from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Annotated, Never
from zoneinfo import ZoneInfo

import typer

from calendar_anim.calendar.capture.final_media import (
    build_exact_audio_extract_command,
    build_mux_command,
    detect_ffmpeg,
    extract_exact_audio,
    mux_audio,
    probe_av_media,
    validate_av_media,
)
from calendar_anim.calendar.cayde_216.artifacts import Cayde216Store
from calendar_anim.calendar.cayde_216.models import Cayde216RemotePreflight
from calendar_anim.calendar.cayde_216.planner import (
    FIRST_WEEK,
    FRAME_COUNT,
    RUN_ID,
    build_cayde_216_plan,
    protected_hashes,
)
from calendar_anim.calendar.hybrid_capture.artifacts import parse_output_resolution
from calendar_anim.calendar.hybrid_capture.media import (
    build_final_visual_command,
    compose_final_visual,
    inspect_final_frames,
    probe_final_visual,
    validate_final_visual_probe,
)
from calendar_anim.calendar.hybrid_capture.models import HybridOutputMode
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


def compose_final_cayde_216_command(
    run_id: Annotated[str, typer.Option("--run-id")] = RUN_ID,
    mode: Annotated[HybridOutputMode, typer.Option("--mode")] = (
        HybridOutputMode.HEADER_PRESERVED_FILL
    ),
    resolution: Annotated[str, typer.Option("--resolution")] = "1512x864",
) -> None:
    """Compose the future 216-frame capture at exactly 6 FPS; never opens Calendar."""

    try:
        if run_id != RUN_ID or mode is not HybridOutputMode.HEADER_PRESERVED_FILL:
            raise CalendarAnimError("216-frame composition is locked to its run and final mode")
        output_resolution = parse_output_resolution(resolution)
        if output_resolution != (1512, 864):
            raise CalendarAnimError("216-frame composition requires 1512x864")
        runtime = Path("output/216-runs") / run_id
        frames = runtime / "final-frames" / mode.directory_name / resolution
        sequence = inspect_final_frames(frames, output_resolution, frame_count=216)
        tools = detect_ffmpeg()
        final = runtime / "final" / mode.directory_name / resolution
        output = final / "final-video-no-audio.mp4"
        command = build_final_visual_command(tools, frames, output, frame_count=216, fps=6)
        compose_final_visual(
            tools,
            frames,
            output,
            output_resolution,
            frame_count=216,
            fps=6,
        )
        probe = probe_final_visual(tools, output)
        validate_final_visual_probe(
            probe,
            output_resolution,
            expected_frame_count=216,
            expected_fps=6,
            expected_duration_seconds=36,
        )
        payload = {
            "input_directory": str(frames),
            "count": sequence.count,
            "first": sequence.first.name,
            "last": sequence.last.name,
            "fps": 6,
            "duration_seconds": probe.duration_seconds,
            "resolution": resolution,
            "ffmpeg_command": command,
            "output": str(output),
            "validation": "PASS",
            "calendar_touched": False,
        }
        Cayde216Store().save_json_report(final / "visual-composition-report.json", payload)
    except (CalendarAnimError, OSError, RuntimeError, ValueError) as error:
        _fail(error)
    typer.echo(f"Frames: {sequence.count}; {sequence.first.name} -> {sequence.last.name}")
    typer.echo("FPS/duration: 6 / 36.000000s")
    typer.echo(f"Visual MP4: {output}")
    typer.echo("Google Calendar reads/writes: NO")


def mux_final_cayde_216_audio_command(
    run_id: Annotated[str, typer.Option("--run-id")] = RUN_ID,
    mode: Annotated[HybridOutputMode, typer.Option("--mode")] = (
        HybridOutputMode.HEADER_PRESERVED_FILL
    ),
    resolution: Annotated[str, typer.Option("--resolution")] = "1512x864",
    source_video: Annotated[Path, typer.Option("--source-video")] = Path("input.mp4"),
) -> None:
    """Mux exact 114-150s audio into the future 216-frame visual by video stream copy."""

    try:
        if run_id != RUN_ID or mode is not HybridOutputMode.HEADER_PRESERVED_FILL:
            raise CalendarAnimError("216-frame mux is locked to its run and final mode")
        output_resolution = parse_output_resolution(resolution)
        runtime = Path("output/216-runs") / run_id
        final = runtime / "final" / mode.directory_name / resolution
        visual = final / "final-video-no-audio.mp4"
        if not visual.is_file():
            raise CalendarAnimError(f"216-frame silent MP4 does not exist: {visual}")
        tools = detect_ffmpeg()
        audio = final / "cutscene-audio-114s-150s.m4a"
        audio_command = build_exact_audio_extract_command(tools, source_video, audio, 114.0, 150.0)
        extract_exact_audio(tools, source_video, audio, 114.0, 150.0)
        output = final / "final-with-audio.mp4"
        mux_command = build_mux_command(tools, visual, audio, output)
        mux_audio(tools, visual, audio, output)
        probe = probe_av_media(tools, output)
        validate_av_media(
            probe,
            output_resolution,
            expected_duration_seconds=36,
            expected_fps=6,
            expected_video_frame_count=216,
        )
        payload = {
            "visual": str(visual),
            "source": str(source_video),
            "clip": [114.0, 150.0],
            "audio_extract_command": audio_command,
            "mux_command": mux_command,
            "video_copied": True,
            "audio_reencoded": True,
            "video_duration": probe.video_duration_seconds,
            "audio_duration": probe.audio_duration_seconds,
            "av_delta": probe.av_delta_seconds,
            "output": str(output),
            "validation": "PASS",
            "calendar_touched": False,
        }
        Cayde216Store().save_json_report(final / "audio-mux-report.json", payload)
    except (CalendarAnimError, OSError, RuntimeError, ValueError) as error:
        _fail(error)
    typer.echo(f"Final with audio: {output}")
    typer.echo(f"A/V delta: {probe.av_delta_seconds:.6f}s")
    typer.echo("Google Calendar reads/writes: NO")


def register_cayde_216_commands(app: typer.Typer) -> None:
    app.command("prepare-cayde-216")(prepare_cayde_216_command)
    app.command("preflight-cayde-216")(preflight_cayde_216_command)
    app.command("compose-final-cayde-216")(compose_final_cayde_216_command)
    app.command("mux-final-cayde-216-audio")(mux_final_cayde_216_audio_command)
