import json
from collections.abc import Callable
from contextlib import AbstractContextManager
from pathlib import Path
from typing import Annotated, Never

import typer

from calendar_anim.browser.playwright_gateway import PlaywrightCalendarCaptureGateway
from calendar_anim.calendar.capture.final_media import (
    detect_ffmpeg,
    extract_audio,
    mux_audio,
    probe_audio_codec,
    probe_duration,
    validate_timing,
)
from calendar_anim.calendar.capture.models import BrowserChannel, CalendarCaptureConfig
from calendar_anim.calendar.hybrid_capture.artifacts import (
    HybridCaptureStore,
    parse_output_resolution,
    resolution_name,
    write_atomic,
)
from calendar_anim.calendar.hybrid_capture.media import compose_final_visual
from calendar_anim.calendar.hybrid_capture.models import HybridOutputMode
from calendar_anim.calendar.hybrid_capture.planner import (
    build_final_capture_plan,
    parse_human_frames,
)
from calendar_anim.calendar.hybrid_capture.service import (
    HybridBrowserGateway,
    HybridCaptureService,
)
from calendar_anim.calendar.profiles.store import CalendarProfileStore
from calendar_anim.calendar.recurrence_compaction.hybrid import (
    FINAL_HYBRID_RUN_ID,
    validate_input_hash,
)
from calendar_anim.calendar.recurrence_upload.artifacts import RecurrenceUploadStore
from calendar_anim.exceptions import CalendarAnimError

SANITY_FRAMES = "24,40,60,80,100,108"


def _fail(error: Exception) -> Never:
    typer.secho(f"Error: {error}", fg=typer.colors.RED, err=True)
    raise typer.Exit(code=1)


def _gateway_factory(
    stabilization_seconds: float, ready_timeout_seconds: float
) -> Callable[[str, int], AbstractContextManager[HybridBrowserGateway]]:
    profiles = CalendarProfileStore()

    def factory(
        profile_name: str, expected_zoom: int
    ) -> AbstractContextManager[HybridBrowserGateway]:
        profile = profiles.load(profile_name)
        if profile.capture_zoom_percent != expected_zoom:
            raise CalendarAnimError(
                f"Profile {profile_name} zoom is {profile.capture_zoom_percent}%, "
                f"expected {expected_zoom}%"
            )
        config = CalendarCaptureConfig(
            profile_directory=profile.browser_profile_directory,
            browser_zoom_percent=profile.capture_zoom_percent,
            visible_start_hour=6,
            visible_end_hour=24,
            stabilization_seconds=stabilization_seconds,
            ready_timeout_seconds=ready_timeout_seconds,
            browser_channel=BrowserChannel.CHROME,
        )
        return PlaywrightCalendarCaptureGateway(config)  # type: ignore[return-value]

    return factory


def capture_hybrid_sanity_command(
    run_id: Annotated[str, typer.Option("--run-id")] = FINAL_HYBRID_RUN_ID,
    profile: Annotated[str, typer.Option("--profile")] = "account-b",
    frames: Annotated[str, typer.Option("--frames")] = SANITY_FRAMES,
    stabilization_seconds: Annotated[float, typer.Option("--stabilization-seconds", min=0)] = 2,
    ready_timeout_seconds: Annotated[float, typer.Option("--ready-timeout-seconds", min=1)] = 90,
    execute: Annotated[bool, typer.Option("--execute")] = False,
) -> None:
    """Capture a small read-only Account-B visual sanity set."""

    if profile != "account-b":
        _fail(CalendarAnimError("Hybrid sanity capture is restricted to account-b"))
    try:
        selected = parse_human_frames(frames)
        plan = build_final_capture_plan(run_id)
        store = HybridCaptureStore()
    except (CalendarAnimError, OSError, ValueError) as error:
        _fail(error)
    typer.echo(f"Run: {run_id}")
    print_final_bulk_status(run_id)
    typer.echo("Profile: account-b")
    typer.echo("Calendar: Calendar Animation Lab B")
    typer.echo("Browser zoom: 90%")
    typer.echo("Vertical scroller: required, 06:00-00:00")
    typer.echo(f"Human frames: {', '.join(str(value) for value in selected)}")
    typer.echo(f"Execution: {'READ-ONLY BROWSER' if execute else 'DRY RUN'}")
    typer.echo(f"Output: {store.sanity_directory(run_id)}")
    if not execute:
        typer.echo("No browser was opened and no Calendar API call was made.")
        typer.echo("Calendar writes: NO")
        return
    try:
        archived = store.archive_sanity(run_id)
        if archived is not None:
            typer.echo(f"Previous sanity archived: {archived}")
        report = HybridCaptureService(
            store, _gateway_factory(stabilization_seconds, ready_timeout_seconds)
        ).capture_sanity(plan, selected)
    except KeyboardInterrupt:
        typer.secho("Sanity capture interrupted; Calendar was not modified.", fg="yellow")
        raise typer.Exit(code=130) from None
    except (CalendarAnimError, OSError, RuntimeError, ValueError) as error:
        _fail(error)
    typer.echo(f"Automated sanity: {report.automated_result}")
    typer.echo(
        f"Contact sheet: {store.sanity_directory(run_id) / 'hybrid-sanity-contact-sheet.png'}"
    )
    typer.echo(f"Report: {store.sanity_directory(run_id) / 'sanity-report.json'}")
    typer.echo("Visual approval is still required before full capture.")
    typer.echo("Calendar writes: NO")


def capture_hybrid_debug_command(
    run_id: Annotated[str, typer.Option("--run-id")] = FINAL_HYBRID_RUN_ID,
    profile: Annotated[str, typer.Option("--profile")] = "account-b",
    frame: Annotated[int, typer.Option("--frame", min=1, max=108)] = 60,
    stabilization_seconds: Annotated[float, typer.Option("--stabilization-seconds", min=0)] = 2,
    ready_timeout_seconds: Annotated[float, typer.Option("--ready-timeout-seconds", min=1)] = 90,
    execute: Annotated[bool, typer.Option("--execute")] = False,
) -> None:
    """Capture exactly one hybrid frame with read-only browser diagnostics."""

    try:
        plan = build_final_capture_plan(run_id)
        if not 1 <= frame <= len(plan.frames):
            raise CalendarAnimError(f"Frame must be between 1 and {len(plan.frames)}")
        selected = plan.frames[frame - 1]
        if selected.calendar_profile != profile:
            raise CalendarAnimError(
                f"Frame {frame} belongs to {selected.calendar_profile}, not {profile}"
            )
        store = HybridCaptureStore()
        output = store.debug_frame_directory(run_id, frame)
    except (CalendarAnimError, OSError, ValueError) as error:
        _fail(error)
    typer.echo(f"Run: {run_id}")
    typer.echo(f"Human frame: {frame}")
    typer.echo(f"Week: {selected.week_start}")
    typer.echo(f"Profile: {profile}")
    typer.echo(f"Browser zoom: {selected.capture_zoom_percent}%")
    typer.echo("Visible window: 06:00-00:00")
    typer.echo("DOM event count: diagnostic only")
    typer.echo(f"Execution: {'READ-ONLY BROWSER' if execute else 'DRY RUN'}")
    typer.echo(f"Output: {output}")
    if not execute:
        typer.echo("No browser was opened and no Calendar API call was made.")
        typer.echo("Calendar writes: NO")
        return
    try:
        report = HybridCaptureService(
            store, _gateway_factory(stabilization_seconds, ready_timeout_seconds)
        ).capture_debug(plan, frame, profile)
    except KeyboardInterrupt:
        typer.secho("Debug capture interrupted; Calendar was not modified.", fg="yellow")
        raise typer.Exit(code=130) from None
    except (CalendarAnimError, OSError, RuntimeError, ValueError) as error:
        _fail(error)
    artifacts = report["artifacts"]
    if not isinstance(artifacts, dict):
        _fail(CalendarAnimError("Debug capture artifact report is invalid"))
    typer.echo(f"Raw browser: {artifacts['raw_browser']}")
    typer.echo(f"Grid crop: {artifacts['grid_crop']}")
    typer.echo(f"Normalized: {artifacts['normalized']}")
    typer.echo(f"Debug JSON: {artifacts['debug_json']}")
    typer.echo("Calendar writes: NO")


def capture_hybrid_debug_modes_command(
    run_id: Annotated[str, typer.Option("--run-id")] = FINAL_HYBRID_RUN_ID,
    profile: Annotated[str, typer.Option("--profile")] = "account-b",
    frame: Annotated[int, typer.Option("--frame", min=1, max=108)] = 60,
    resolution: Annotated[str, typer.Option("--resolution")] = "504x288",
    stabilization_seconds: Annotated[float, typer.Option("--stabilization-seconds", min=0)] = 2,
    ready_timeout_seconds: Annotated[float, typer.Option("--ready-timeout-seconds", min=1)] = 90,
    execute: Annotated[bool, typer.Option("--execute")] = False,
) -> None:
    """Capture one frame and locally compare all three final output modes."""

    try:
        plan = build_final_capture_plan(run_id)
        output_resolution = parse_output_resolution(resolution)
        selected = plan.frames[frame - 1]
        if selected.calendar_profile != profile:
            raise CalendarAnimError(
                f"Frame {frame} belongs to {selected.calendar_profile}, not {profile}"
            )
        store = HybridCaptureStore()
        output = (
            store.high_resolution_debug_directory(run_id, frame)
            if output_resolution != (504, 288)
            else store.debug_frame_directory(run_id, frame)
        )
    except (CalendarAnimError, IndexError, OSError, ValueError) as error:
        _fail(error)
    typer.echo(f"Run: {run_id}")
    typer.echo(f"Human frame: {frame}")
    typer.echo(f"Week: {selected.week_start}")
    typer.echo(f"Profile: {profile}")
    typer.echo(f"Browser zoom: {selected.capture_zoom_percent}%")
    typer.echo(f"Output resolution: {resolution_name(output_resolution)}")
    if output_resolution == (504, 288):
        typer.echo("Modes: pixel_faithful, header_preserved_letterbox, header_preserved_fill")
    else:
        typer.echo("Mode: header_preserved_fill (native high-resolution path)")
    typer.echo("Visible window: 06:00-00:00")
    typer.echo(f"Execution: {'READ-ONLY BROWSER' if execute else 'DRY RUN'}")
    typer.echo(f"Output: {output}")
    if not execute:
        typer.echo("No browser was opened and no Calendar API call was made.")
        typer.echo("Calendar writes: NO")
        return
    try:
        report = HybridCaptureService(
            store, _gateway_factory(stabilization_seconds, ready_timeout_seconds)
        ).capture_debug_modes(plan, frame, profile, output_resolution)
    except KeyboardInterrupt:
        typer.secho("Mode comparison interrupted; Calendar was not modified.", fg="yellow")
        raise typer.Exit(code=130) from None
    except (CalendarAnimError, OSError, RuntimeError, ValueError) as error:
        _fail(error)
    artifacts = report.get("artifacts")
    if not isinstance(artifacts, dict):
        _fail(CalendarAnimError("Mode comparison artifact report is invalid"))
    if output_resolution == (504, 288):
        typer.echo(f"Mode A: {output / 'mode-a-pixel-faithful.png'}")
        typer.echo(f"Mode B: {output / 'mode-b-header-preserved-letterbox.png'}")
        typer.echo(f"Mode C: {output / 'mode-c-header-preserved-fill.png'}")
    else:
        typer.echo(f"Native crop: {artifacts['native_crop']}")
        typer.echo(f"Mode C high resolution: {artifacts['final_output']}")
    typer.echo(f"Comparison: {artifacts['comparison']}")
    typer.echo(f"Debug JSON: {artifacts['debug_json']}")
    typer.echo("Calendar writes: NO")


def capture_final_hybrid_command(
    run_id: Annotated[str, typer.Option("--run-id")] = FINAL_HYBRID_RUN_ID,
    mode: Annotated[HybridOutputMode, typer.Option("--mode")] = (HybridOutputMode.PIXEL_FAITHFUL),
    resolution: Annotated[str, typer.Option("--resolution")] = "504x288",
    stabilization_seconds: Annotated[float, typer.Option("--stabilization-seconds", min=0)] = 2,
    ready_timeout_seconds: Annotated[float, typer.Option("--ready-timeout-seconds", min=1)] = 90,
    execute: Annotated[bool, typer.Option("--execute")] = False,
) -> None:
    """Capture all 108 frames with the locked A/B profile boundary."""

    try:
        plan = build_final_capture_plan(run_id)
        output_resolution = parse_output_resolution(resolution)
        store = HybridCaptureStore()
        state = store.initialize_state(plan, mode, output_resolution)
        sanity_path = store.sanity_directory(run_id) / "sanity-report.json"
        sanity = store.load_sanity_report(run_id) if sanity_path.exists() else None
    except (CalendarAnimError, OSError, ValueError) as error:
        _fail(error)
    typer.echo(f"Run: {run_id}")
    typer.echo("Frames 1-23: account-a, zoom 33%")
    typer.echo("Frames 24-108: account-b, zoom 90%")
    typer.echo("Visible window: 06:00-00:00")
    typer.echo(f"Output mode: {mode.value}")
    typer.echo(f"Normalized output: 108 PNGs, {resolution_name(output_resolution)}")
    typer.echo(f"Sanity gate: {sanity.automated_result if sanity else 'NOT RUN'}")
    typer.echo(f"Execution: {'READ-ONLY BROWSER' if execute else 'DRY RUN'}")
    typer.echo(f"Output: {store.final_frames_directory(run_id, mode, output_resolution)}")
    if not execute:
        typer.echo("No browser was opened and no Calendar API call was made.")
        typer.echo("Calendar writes: NO")
        return
    try:
        state = HybridCaptureService(
            store, _gateway_factory(stabilization_seconds, ready_timeout_seconds)
        ).capture_final(plan, state, mode, output_resolution)
    except KeyboardInterrupt:
        typer.secho("Capture interrupted; atomic frame checkpoint was preserved.", fg="yellow")
        raise typer.Exit(code=130) from None
    except (CalendarAnimError, OSError, RuntimeError, ValueError) as error:
        _fail(error)
    completed = sum(item.status.value == "completed" for item in state.frames)
    typer.echo(f"Completed: {completed}/108")
    seam = (
        store.run_directory(run_id) / "seam" / mode.directory_name / "a-b-transition-geometry.png"
    )
    typer.echo(f"A/B seam: {seam}")
    typer.echo("Calendar writes: NO")


def capture_hybrid_seam_command(
    run_id: Annotated[str, typer.Option("--run-id")] = FINAL_HYBRID_RUN_ID,
    stabilization_seconds: Annotated[float, typer.Option("--stabilization-seconds", min=0)] = 2,
    ready_timeout_seconds: Annotated[float, typer.Option("--ready-timeout-seconds", min=1)] = 90,
    execute: Annotated[bool, typer.Option("--execute")] = False,
) -> None:
    """Capture only human frames 23/A and 24/B for geometry validation."""

    try:
        plan = build_final_capture_plan(run_id)
        store = HybridCaptureStore()
        sanity_path = store.sanity_directory(run_id) / "sanity-report.json"
        sanity = store.load_sanity_report(run_id) if sanity_path.exists() else None
    except (CalendarAnimError, OSError, ValueError) as error:
        _fail(error)
    typer.echo("Frame 23: account-a, zoom 33%")
    typer.echo("Frame 24: account-b, zoom 90%")
    typer.echo("Normalization: logical 126x72 grid to 504x288")
    typer.echo(f"Sanity gate: {sanity.automated_result if sanity else 'NOT RUN'}")
    typer.echo(f"Execution: {'READ-ONLY BROWSER' if execute else 'DRY RUN'}")
    typer.echo(f"Output: {store.seam_directory(run_id)}")
    if not execute:
        typer.echo("No browser was opened and no Calendar API call was made.")
        typer.echo("Calendar writes: NO")
        return
    try:
        report = HybridCaptureService(
            store, _gateway_factory(stabilization_seconds, ready_timeout_seconds)
        ).capture_seam(plan)
    except KeyboardInterrupt:
        typer.secho("Seam capture interrupted; Calendar was not modified.", fg="yellow")
        raise typer.Exit(code=130) from None
    except (CalendarAnimError, OSError, RuntimeError, ValueError) as error:
        _fail(error)
    typer.echo(f"Geometry result: {report.geometry_result}")
    typer.echo(f"Cell width delta: {report.cell_width_relative_delta:.3%}")
    typer.echo(f"Cell height delta: {report.cell_height_relative_delta:.3%}")
    typer.echo(f"Comparison: {store.seam_directory(run_id) / 'a-b-transition-geometry.png'}")
    typer.echo("Calendar writes: NO")


def compose_final_hybrid_command(
    run_id: Annotated[str, typer.Option("--run-id")] = FINAL_HYBRID_RUN_ID,
    mode: Annotated[HybridOutputMode, typer.Option("--mode")] = (HybridOutputMode.PIXEL_FAITHFUL),
    resolution: Annotated[str, typer.Option("--resolution")] = "504x288",
) -> None:
    """Compose 108 normalized PNG frames into the approved silent MP4."""

    try:
        store = HybridCaptureStore()
        output_resolution = parse_output_resolution(resolution)
        plan = store.load_plan(run_id)
        if plan.frame_count / plan.fps != 36:
            raise CalendarAnimError("108 frames at 3 FPS must equal exactly 36 seconds")
        tools = detect_ffmpeg()
        output = store.final_directory(run_id, mode, output_resolution) / "calendar-animation.mp4"
        compose_final_visual(
            tools,
            store.final_frames_directory(run_id, mode, output_resolution),
            output,
            output_resolution,
        )
        duration = probe_duration(tools, output)
        if abs(duration - 36) > 0.05:
            raise CalendarAnimError(f"Silent MP4 duration is {duration:.6f}s, expected 36s")
        report = {
            "frames": 108,
            "fps": 3,
            "duration_seconds": duration,
            "resolution": resolution_name(output_resolution),
            "codec": "H.264 High",
            "crf": 10,
            "preset": "slow",
            "pixel_format": "yuv420p",
            "sar": "1:1",
            "source": "normalized Calendar PNG screenshots",
            "output_mode": mode.value,
            "header_included": mode.includes_header,
            "letterbox": mode is HybridOutputMode.HEADER_PRESERVED_LETTERBOX,
            "non_uniform_scaling_allowed": mode is HybridOutputMode.HEADER_PRESERVED_FILL,
        }
        write_atomic(
            output.parent / "visual-composition-report.json", json.dumps(report, indent=2) + "\n"
        )
    except (CalendarAnimError, OSError, RuntimeError, ValueError) as error:
        _fail(error)
    typer.echo(f"Visual MP4: {output}")
    typer.echo(f"Duration: {duration:.6f}s")


def mux_final_hybrid_audio_command(
    run_id: Annotated[str, typer.Option("--run-id")] = FINAL_HYBRID_RUN_ID,
    mode: Annotated[HybridOutputMode, typer.Option("--mode")] = (HybridOutputMode.PIXEL_FAITHFUL),
    resolution: Annotated[str, typer.Option("--resolution")] = "504x288",
    source_video: Annotated[Path, typer.Option("--source-video")] = Path("input.mp4"),
) -> None:
    """Mux the exact 114-150s source audio with the composed Calendar MP4."""

    try:
        validate_input_hash(source_video)
        output_resolution = parse_output_resolution(resolution)
        store = HybridCaptureStore()
        final = store.final_directory(run_id, mode, output_resolution)
        visual = final / "calendar-animation.mp4"
        if not visual.is_file():
            raise CalendarAnimError("Compose the silent final hybrid MP4 first")
        tools = detect_ffmpeg()
        codec = probe_audio_codec(tools, source_video)
        audio = extract_audio(
            tools,
            source_video,
            final / "cutscene-audio.m4a",
            114.0,
            150.0,
            source_audio_codec=codec,
        )
        output = mux_audio(tools, visual, audio, final / "final-with-audio.mp4")
        timing = validate_timing(
            probe_duration(tools, visual),
            probe_duration(tools, audio),
            probe_duration(tools, output),
        )
        report = {
            "source": str(source_video),
            "source_audio_codec": codec,
            "clip_start_seconds": 114.0,
            "clip_end_seconds": 150.0,
            "expected_duration_seconds": 36.0,
            "visual_duration_seconds": timing.visual_seconds,
            "audio_duration_seconds": timing.audio_seconds,
            "final_duration_seconds": timing.final_seconds,
            "output": str(output),
        }
        write_atomic(final / "audio-mux-report.json", json.dumps(report, indent=2) + "\n")
    except (CalendarAnimError, OSError, RuntimeError, ValueError) as error:
        _fail(error)
    typer.echo(f"Final with audio: {output}")
    typer.echo(f"Duration: {timing.final_seconds:.6f}s")


def print_final_bulk_status(run_id: str = FINAL_HYBRID_RUN_ID) -> None:
    state = RecurrenceUploadStore().load_state(run_id)
    typer.echo(f"Parents completed: {state.completed_count}/{len(state.parents)}")
    typer.echo(f"rateLimitExceeded: {state.rate_limit_exceeded_count}")
    typer.echo(f"quotaExceeded: {state.quota_exceeded_count}")


def register_hybrid_capture_commands(app: typer.Typer) -> None:
    app.command("capture-hybrid-debug")(capture_hybrid_debug_command)
    app.command("capture-hybrid-debug-modes")(capture_hybrid_debug_modes_command)
    app.command("capture-hybrid-sanity")(capture_hybrid_sanity_command)
    app.command("capture-hybrid-seam")(capture_hybrid_seam_command)
    app.command("capture-final-hybrid")(capture_final_hybrid_command)
    app.command("compose-final-hybrid")(compose_final_hybrid_command)
    app.command("mux-final-hybrid-audio")(mux_final_hybrid_audio_command)
