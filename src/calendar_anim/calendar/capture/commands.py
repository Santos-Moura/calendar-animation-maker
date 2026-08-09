import re
from pathlib import Path
from typing import Annotated, Never

import typer

from calendar_anim.browser.login import launch_manual_login_browser
from calendar_anim.browser.playwright_gateway import PlaywrightCalendarCaptureGateway
from calendar_anim.calendar.capture.artifacts import (
    CaptureStore,
    build_capture_plan,
)
from calendar_anim.calendar.capture.composition import (
    compose_gif,
    compose_mp4,
    validate_completed_capture,
)
from calendar_anim.calendar.capture.models import (
    BrowserChannel,
    CalendarCaptureConfig,
    CaptureState,
    FrameCaptureStatus,
)
from calendar_anim.calendar.capture.service import CalendarWeekCaptureService
from calendar_anim.calendar.multi_frame.artifacts import AnimationRunStore
from calendar_anim.exceptions import CalendarAnimError


def _fail(error: Exception) -> Never:
    typer.secho(f"Error: {error}", fg=typer.colors.RED, err=True)
    raise typer.Exit(code=1)


def _valid_run_id(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", value):
        raise CalendarAnimError(f"Invalid run-id: {value!r}")
    return value


def _capture_config(
    profile_directory: Path,
    stabilization_seconds: float,
    ready_timeout_seconds: float,
    browser_channel: BrowserChannel,
) -> CalendarCaptureConfig:
    return CalendarCaptureConfig(
        profile_directory=profile_directory,
        stabilization_seconds=stabilization_seconds,
        ready_timeout_seconds=ready_timeout_seconds,
        browser_channel=browser_channel,
    )


def browser_login_command(
    profile_directory: Annotated[Path, typer.Option("--profile-directory")] = Path(
        ".calendar-anim/browser-profile"
    ),
    browser_executable: Annotated[Path | None, typer.Option("--browser-executable")] = None,
) -> None:
    """Open normal Chrome for a one-time manual Google login and UI setup."""
    typer.echo(f"Persistent browser profile: {profile_directory}")
    typer.echo("Browser mode: normal Google Chrome (not controlled by Playwright).")
    typer.echo("No Google credentials will be read or typed by this command.")
    try:
        launch_manual_login_browser(profile_directory, browser_executable)
        typer.echo("Log in manually and configure week view, dark theme, and hidden sidebar.")
        typer.echo("Close that Chrome window completely when Calendar is ready.")
        typer.prompt("Press Enter here after closing Chrome", default="", show_default=False)
    except (CalendarAnimError, OSError, RuntimeError) as error:
        _fail(error)
    typer.echo("Browser profile saved and ready for Playwright capture.")


def capture_animation_command(
    run_id: Annotated[str, typer.Option("--run-id")],
    animation_output_root: Annotated[Path, typer.Option("--animation-output-root")] = Path(
        "output/animation-runs"
    ),
    capture_output_root: Annotated[Path, typer.Option("--capture-output-root")] = Path(
        "output/captures"
    ),
    profile_directory: Annotated[Path, typer.Option("--profile-directory")] = Path(
        ".calendar-anim/browser-profile"
    ),
    stabilization_seconds: Annotated[float, typer.Option("--stabilization-seconds", min=0)] = 2.0,
    ready_timeout_seconds: Annotated[float, typer.Option("--ready-timeout-seconds", min=1)] = 30.0,
    browser_channel: Annotated[
        BrowserChannel, typer.Option("--browser-channel")
    ] = BrowserChannel.CHROME,
    recapture: Annotated[
        bool,
        typer.Option(
            "--recapture",
            help="Back up existing screenshots and recapture every frame with --execute.",
        ),
    ] = False,
    execute: Annotated[
        bool, typer.Option("--execute", help="Open the browser and capture screenshots.")
    ] = False,
) -> None:
    """Plan or execute resumable screenshots for uploaded animation weeks."""
    try:
        resolved_run_id = _valid_run_id(run_id)
        config = _capture_config(
            profile_directory,
            stabilization_seconds,
            ready_timeout_seconds,
            browser_channel,
        )
        animation_store = AnimationRunStore(animation_output_root)
        plan = build_capture_plan(resolved_run_id, animation_store, config)
        store = CaptureStore(capture_output_root)
        state = store.initialize(plan)
    except (CalendarAnimError, OSError, ValueError) as error:
        _fail(error)
    typer.echo(f"Animation: {plan.run_id}")
    typer.echo(f"Frames: {plan.frame_count}")
    typer.echo(f"Weeks: {plan.frames[0].week_start} through {plan.frames[-1].week_start}")
    typer.echo(f"Current state: {_status_counts(state)}")
    typer.echo(f"Execution: {'REAL BROWSER' if execute else 'DRY RUN'}")
    typer.echo(f"Artifacts: {store.run_directory(plan.run_id)}")
    if not execute:
        typer.echo("\nPlanned actions:")
        for frame in state.frames:
            if recapture:
                action = "RECAPTURE (backup occurs only with --execute)"
            else:
                action = (
                    "SKIP (completed)"
                    if frame.status is FrameCaptureStatus.COMPLETED
                    else "CAPTURE"
                )
            typer.echo(f"Frame {frame.frame_index}: {action}")
        typer.echo("No browser was opened and no Calendar API call was made.")
        return

    def progress(frame_index: int, status: FrameCaptureStatus) -> None:
        typer.echo(f"Frame {frame_index}: {status.value}")

    try:
        if recapture:
            backup = store.reset_for_recapture(plan, state)
            typer.echo(f"Previous capture backup: {backup or 'no previous files'}")
        with PlaywrightCalendarCaptureGateway(config) as gateway:
            state = CalendarWeekCaptureService(gateway, store, progress).capture(plan, state)
    except KeyboardInterrupt:
        typer.secho("Capture interrupted; checkpoint was preserved.", fg=typer.colors.RED)
        raise typer.Exit(code=130) from None
    except (CalendarAnimError, OSError, RuntimeError) as error:
        _fail(error)
    typer.echo(f"Capture progress: {_status_counts(state)}")
    typer.echo("Calendar events were not created, changed, or deleted.")


def compose_capture_command(
    run_id: Annotated[str, typer.Option("--run-id")],
    fps: Annotated[float, typer.Option("--fps", min=0.01)] = 3.0,
    capture_output_root: Annotated[Path, typer.Option("--capture-output-root")] = Path(
        "output/captures"
    ),
    mp4: Annotated[
        bool, typer.Option("--mp4", help="Also compose an H.264 MP4 using ffmpeg.")
    ] = False,
) -> None:
    """Compose completed screenshots into a GIF and optionally an H.264 MP4."""
    try:
        store = CaptureStore(capture_output_root)
        plan = store.load_plan(_valid_run_id(run_id))
        state = store.load_state(plan.run_id)
        frame_paths = validate_completed_capture(plan, state, store)
        output_directory = store.run_directory(plan.run_id)
        gif_path = compose_gif(frame_paths, output_directory / "animation.gif", fps)
        mp4_path = (
            compose_mp4(frame_paths, output_directory / "animation.mp4", fps) if mp4 else None
        )
    except (CalendarAnimError, OSError, ValueError) as error:
        _fail(error)
    typer.echo(f"Frames: {len(frame_paths)}")
    typer.echo(f"FPS: {fps:g}")
    typer.echo(f"GIF: {gif_path}")
    if mp4_path is not None:
        typer.echo(f"MP4: {mp4_path}")
    typer.echo("Repeated consecutive screenshots keep their full playback duration.")


def _status_counts(state: CaptureState) -> str:
    frames = state.frames
    completed = sum(frame.status is FrameCaptureStatus.COMPLETED for frame in frames)
    failed = sum(frame.status is FrameCaptureStatus.FAILED for frame in frames)
    capturing = sum(frame.status is FrameCaptureStatus.CAPTURING for frame in frames)
    pending = len(frames) - completed - failed - capturing
    return (
        f"{completed}/{len(frames)} completed, {pending} pending, "
        f"{capturing} capturing, {failed} failed"
    )


def register_capture_commands(app: typer.Typer) -> None:
    app.command("browser-login")(browser_login_command)
    app.command("capture-animation")(capture_animation_command)
    app.command("compose-capture")(compose_capture_command)
