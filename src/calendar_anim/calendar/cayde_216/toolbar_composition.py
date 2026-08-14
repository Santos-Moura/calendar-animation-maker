import hashlib
import json
from datetime import timedelta
from pathlib import Path
from typing import Annotated, Never

import typer
from PIL import Image, ImageDraw

from calendar_anim.calendar.capture.final_media import (
    build_exact_audio_extract_command,
    build_mux_command,
    detect_ffmpeg,
    extract_exact_audio,
    mux_audio,
    probe_av_media,
    validate_av_media,
)
from calendar_anim.calendar.cayde_216.artifacts import write_atomic
from calendar_anim.calendar.cayde_216.planner import FIRST_WEEK, FRAME_COUNT, RUN_ID
from calendar_anim.calendar.hybrid_capture.artifacts import (
    HybridCaptureStore,
    parse_output_resolution,
    resolution_name,
)
from calendar_anim.calendar.hybrid_capture.media import (
    build_final_visual_command,
    compose_final_visual,
    inspect_final_frames,
    probe_final_visual,
    validate_final_visual_probe,
)
from calendar_anim.calendar.hybrid_capture.models import (
    HybridFrameStatus,
    HybridOutputMode,
)
from calendar_anim.exceptions import CalendarAnimError

PREVIEW_HUMAN_FRAME = 108
PREVIEW_FRAME_INDEX = PREVIEW_HUMAN_FRAME - 1
CAPTURE_RESOLUTION = (1512, 864)
NATIVE_TOOLBAR_HEIGHT = 58
NATIVE_WEEK_HEADER_HEIGHT = 75
NATIVE_TIME_GUTTER_WIDTH = 73


def _fail(error: Exception) -> Never:
    typer.secho(f"Error: {error}", fg=typer.colors.RED, err=True)
    raise typer.Exit(code=1)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def compose_calendar_toolbar_frame(
    raw_browser: Path,
    native_header_grid: Path,
    output: Path,
    *,
    toolbar_artifact: Path | None = None,
    resolution: tuple[int, int] = CAPTURE_RESOLUTION,
) -> dict[str, object]:
    """Add Calendar's app toolbar above the approved header/gutter/grid composition."""

    try:
        with Image.open(raw_browser) as opened:
            raw = opened.convert("RGB")
        with Image.open(native_header_grid) as opened:
            source = opened.convert("RGB")
    except OSError as error:
        raise CalendarAnimError("Could not load Calendar toolbar composition sources") from error
    try:
        source_width, source_height = source.size
        raw_dimensions = list(raw.size)
        if (
            raw.width < source_width
            or raw.height < NATIVE_TOOLBAR_HEIGHT
            or source_height <= NATIVE_WEEK_HEADER_HEIGHT
            or source_width <= NATIVE_TIME_GUTTER_WIDTH
        ):
            raise CalendarAnimError("Calendar toolbar composition source geometry is invalid")
        grid_height = source_height - NATIVE_WEEK_HEADER_HEIGHT
        composite_height = NATIVE_TOOLBAR_HEIGHT + source_height
        target_width, target_height = resolution
        target_toolbar_height = max(
            1, round(target_height * NATIVE_TOOLBAR_HEIGHT / composite_height)
        )
        target_header_height = max(
            1, round(target_height * NATIVE_WEEK_HEADER_HEIGHT / composite_height)
        )
        target_grid_height = target_height - target_toolbar_height - target_header_height
        target_gutter_width = max(1, round(target_width * NATIVE_TIME_GUTTER_WIDTH / source_width))

        toolbar = raw.crop((0, 0, raw.width, NATIVE_TOOLBAR_HEIGHT))
        weekly_header = source.crop((0, 0, source_width, NATIVE_WEEK_HEADER_HEIGHT))
        time_gutter = source.crop(
            (0, NATIVE_WEEK_HEADER_HEIGHT, NATIVE_TIME_GUTTER_WIDTH, source_height)
        )
        event_grid = source.crop(
            (
                NATIVE_TIME_GUTTER_WIDTH,
                NATIVE_WEEK_HEADER_HEIGHT,
                source_width,
                source_height,
            )
        )
        composed = Image.new("RGB", resolution, "#202124")
        resized_toolbar = toolbar.resize(
            (target_width, target_toolbar_height), Image.Resampling.LANCZOS
        )
        resized_header = weekly_header.resize(
            (target_width, target_header_height), Image.Resampling.LANCZOS
        )
        resized_gutter = time_gutter.resize(
            (target_gutter_width, target_grid_height), Image.Resampling.LANCZOS
        )
        resized_grid = event_grid.resize(
            (target_width - target_gutter_width, target_grid_height),
            Image.Resampling.NEAREST,
        )
        grid_top = target_toolbar_height + target_header_height
        composed.paste(resized_toolbar, (0, 0))
        composed.paste(resized_header, (0, target_toolbar_height))
        composed.paste(resized_gutter, (0, grid_top))
        composed.paste(resized_grid, (target_gutter_width, grid_top))
        output.parent.mkdir(parents=True, exist_ok=True)
        composed.save(output)
        if toolbar_artifact is not None:
            toolbar_artifact.parent.mkdir(parents=True, exist_ok=True)
            toolbar.save(toolbar_artifact)
    finally:
        for image in (
            raw,
            source,
            locals().get("toolbar"),
            locals().get("weekly_header"),
            locals().get("time_gutter"),
            locals().get("event_grid"),
            locals().get("resized_toolbar"),
            locals().get("resized_header"),
            locals().get("resized_gutter"),
            locals().get("resized_grid"),
            locals().get("composed"),
        ):
            if isinstance(image, Image.Image):
                image.close()

    return {
        "native_raw_browser_dimensions": raw_dimensions,
        "native_header_grid_dimensions": [source_width, source_height],
        "native_composite_dimensions": [raw_dimensions[0], composite_height],
        "native_toolbar_rect": [0, 0, raw_dimensions[0], NATIVE_TOOLBAR_HEIGHT],
        "native_week_header_rect": [0, 0, source_width, NATIVE_WEEK_HEADER_HEIGHT],
        "native_time_gutter_rect": [
            0,
            NATIVE_WEEK_HEADER_HEIGHT,
            NATIVE_TIME_GUTTER_WIDTH,
            grid_height,
        ],
        "native_event_grid_rect": [
            NATIVE_TIME_GUTTER_WIDTH,
            NATIVE_WEEK_HEADER_HEIGHT,
            source_width - NATIVE_TIME_GUTTER_WIDTH,
            grid_height,
        ],
        "output_dimensions": list(resolution),
        "output_toolbar_rect": [0, 0, target_width, target_toolbar_height],
        "output_week_header_rect": [
            0,
            target_toolbar_height,
            target_width,
            target_header_height,
        ],
        "output_time_gutter_rect": [0, grid_top, target_gutter_width, target_grid_height],
        "output_event_grid_rect": [
            target_gutter_width,
            grid_top,
            target_width - target_gutter_width,
            target_grid_height,
        ],
        "toolbar_resampling": "lanczos",
        "week_header_resampling": "lanczos",
        "time_gutter_resampling": "lanczos",
        "event_grid_resampling": "nearest-neighbor",
        "resize_passes_per_segment": 1,
        "blur_sharpen_color_correction": False,
    }


def preview_cayde_216_calendar_toolbar_command(
    run_id: Annotated[str, typer.Option("--run-id")] = RUN_ID,
    profile: Annotated[str, typer.Option("--profile")] = "account-b",
    frame: Annotated[int, typer.Option("--frame", min=1, max=216)] = PREVIEW_HUMAN_FRAME,
    mode: Annotated[HybridOutputMode, typer.Option("--mode")] = (
        HybridOutputMode.HEADER_PRESERVED_FILL
    ),
    resolution: Annotated[str, typer.Option("--resolution")] = "1512x864",
) -> None:
    """Build one isolated Calendar-toolbar preview from protected capture components."""

    try:
        if run_id != RUN_ID or profile != "account-b" or frame != PREVIEW_HUMAN_FRAME:
            raise CalendarAnimError("Calendar toolbar preview is locked to Account B frame 108")
        if mode is not HybridOutputMode.HEADER_PRESERVED_FILL:
            raise CalendarAnimError("Calendar toolbar preview requires header_preserved_fill")
        output_resolution = parse_output_resolution(resolution)
        if output_resolution != CAPTURE_RESOLUTION:
            raise CalendarAnimError("Calendar toolbar preview requires resolution 1512x864")
        store = HybridCaptureStore(Path("output/216-runs"))
        plan = store.load_plan(run_id)
        state = store.initialize_state(plan, mode, output_resolution)
        if state.frame(PREVIEW_FRAME_INDEX).status is not HybridFrameStatus.COMPLETED:
            raise CalendarAnimError("Protected frame 108 capture is not completed")
        raw = store.final_raw_path(run_id, PREVIEW_FRAME_INDEX, mode, output_resolution)
        header = store.final_header_path(run_id, PREVIEW_FRAME_INDEX, mode, output_resolution)
        protected = store.final_frame_path(run_id, PREVIEW_FRAME_INDEX, mode, output_resolution)
        for path in (raw, header, protected):
            if not path.is_file():
                raise CalendarAnimError(f"Required protected capture artifact is missing: {path}")
        protected_hash_before = _sha256(protected)
        directory = store.run_directory(run_id) / "calendar-toolbar-preview" / "frame-108"
        output = directory / "calendar-toolbar-preview-frame-108.png"
        toolbar = directory / "calendar-toolbar-native.png"
        comparison = directory / "before-after-comparison.png"
        metrics = compose_calendar_toolbar_frame(
            raw,
            header,
            output,
            toolbar_artifact=toolbar,
            resolution=output_resolution,
        )
        _build_before_after(protected, output, comparison)
        protected_hash_after = _sha256(protected)
        if protected_hash_after != protected_hash_before:
            raise CalendarAnimError("Protected final frame changed during isolated preview")
        future_command = (
            ".\\.venv\\Scripts\\python.exe -m calendar_anim calendar "
            "recompose-final-cayde-216-calendar-toolbar "
            f"--run-id {run_id} --profile account-b --frames 1-216 "
            "--mode header_preserved_fill --resolution 1512x864 --execute"
        )
        report = {
            "run_id": run_id,
            "human_frame": frame,
            "frame_index": PREVIEW_FRAME_INDEX,
            "week_start": (FIRST_WEEK + timedelta(weeks=107)).isoformat(),
            "profile": profile,
            "zoom_percent": 90,
            "mode": mode.value,
            "resolution": list(output_resolution),
            "sources_reused": {"raw_browser": str(raw), "native_header_grid": str(header)},
            "artifacts": {
                "preview": str(output),
                "native_toolbar": str(toolbar),
                "comparison": str(comparison),
                "report_json": str(directory / "report.json"),
                "report_text": str(directory / "report.txt"),
            },
            "composition": metrics,
            "changes_from_previous": {
                "calendar_app_toolbar_added": True,
                "weekly_header_preserved": True,
                "time_gutter_preserved": True,
                "vertical_interval_preserved": "06:00-00:00",
                "previous_output_grid_height": 788,
                "new_output_grid_height": 738,
                "browser_chrome_included": False,
                "operating_system_ui_included": False,
                "create_button_included": False,
                "right_addon_sidebar_included": False,
            },
            "protected_frame_sha256_before": protected_hash_before,
            "protected_frame_sha256_after": protected_hash_after,
            "protected_frame_unchanged": True,
            "future_full_command": future_command,
            "full_capture_started": False,
            "capture_checkpoint_touched": False,
            "browser_opened": False,
            "google_calendar_reads": False,
            "google_calendar_writes": False,
            "recurrence_touched": False,
            "compose_video_started": False,
            "audio_mux_started": False,
        }
        write_atomic(directory / "report.json", json.dumps(report, indent=2) + "\n")
        write_atomic(directory / "report.txt", _preview_report_text(report))
    except (CalendarAnimError, OSError, RuntimeError, ValueError) as error:
        _fail(error)
    typer.echo("CAYDE 216 CALENDAR TOOLBAR PREVIEW")
    typer.echo(f"Frame: {frame}; week: {report['week_start']}")
    typer.echo(f"Native composite: {metrics['native_composite_dimensions']}")
    typer.echo(f"Final dimensions: {metrics['output_dimensions']}")
    typer.echo(f"Preview: {output}")
    typer.echo(f"Comparison: {comparison}")
    typer.echo(f"Report: {directory / 'report.json'}")
    typer.echo("Browser opened: NO")
    typer.echo("Full capture/checkpoint touched: NO")
    typer.echo("Google Calendar writes: NO")


def recompose_final_cayde_216_calendar_toolbar_command(
    run_id: Annotated[str, typer.Option("--run-id")] = RUN_ID,
    profile: Annotated[str, typer.Option("--profile")] = "account-b",
    frames: Annotated[str, typer.Option("--frames")] = "1-216",
    mode: Annotated[HybridOutputMode, typer.Option("--mode")] = (
        HybridOutputMode.HEADER_PRESERVED_FILL
    ),
    resolution: Annotated[str, typer.Option("--resolution")] = "1512x864",
    execute: Annotated[bool, typer.Option("--execute")] = False,
) -> None:
    """Prepare or locally recompose all protected captures with Calendar's toolbar."""

    try:
        if run_id != RUN_ID or profile != "account-b" or frames != "1-216":
            raise CalendarAnimError("Toolbar recomposition is locked to Account B frames 1-216")
        if mode is not HybridOutputMode.HEADER_PRESERVED_FILL:
            raise CalendarAnimError("Toolbar recomposition requires header_preserved_fill")
        output_resolution = parse_output_resolution(resolution)
        if output_resolution != CAPTURE_RESOLUTION:
            raise CalendarAnimError("Toolbar recomposition requires resolution 1512x864")
        store = HybridCaptureStore(Path("output/216-runs"))
        plan = store.load_plan(run_id)
        state = store.initialize_state(plan, mode, output_resolution)
        if len(state.frames) != FRAME_COUNT or any(
            item.status is not HybridFrameStatus.COMPLETED for item in state.frames
        ):
            raise CalendarAnimError("All 216 protected captures must be completed")
        source_pairs = [
            (
                store.final_raw_path(run_id, index, mode, output_resolution),
                store.final_header_path(run_id, index, mode, output_resolution),
            )
            for index in range(FRAME_COUNT)
        ]
        if any(not raw.is_file() or not header.is_file() for raw, header in source_pairs):
            raise CalendarAnimError("One or more protected capture components are missing")
        output_directory = (
            store.run_directory(run_id)
            / "final-frames-calendar-toolbar"
            / mode.directory_name
            / resolution_name(output_resolution)
        )
    except (CalendarAnimError, OSError, RuntimeError, ValueError) as error:
        _fail(error)
    typer.echo("CAYDE 216 CALENDAR TOOLBAR RECOMPOSITION")
    typer.echo("Source frames: 216 protected raw/header components")
    typer.echo(f"Output: {output_directory}")
    typer.echo(f"Execution: {'LOCAL WRITE' if execute else 'DRY RUN'}")
    if not execute:
        typer.echo("Browser opened: NO")
        typer.echo("Google Calendar reads/writes: NO")
        return
    for index, (raw, header) in enumerate(source_pairs):
        output = output_directory / f"frame_{index:03d}.png"
        compose_calendar_toolbar_frame(raw, header, output, resolution=output_resolution)
        typer.echo(f"Frame {index + 1}/{FRAME_COUNT}: completed")
    typer.echo(f"Completed: {FRAME_COUNT}/{FRAME_COUNT}")
    typer.echo("Protected capture artifacts changed: NO")
    typer.echo("Browser opened: NO")
    typer.echo("Google Calendar reads/writes: NO")


def compose_final_cayde_216_calendar_toolbar_command(
    run_id: Annotated[str, typer.Option("--run-id")] = RUN_ID,
    mode: Annotated[HybridOutputMode, typer.Option("--mode")] = (
        HybridOutputMode.HEADER_PRESERVED_FILL
    ),
    resolution: Annotated[str, typer.Option("--resolution")] = "1512x864",
) -> None:
    """Compose the approved Calendar-toolbar frame sequence at 6 FPS."""

    try:
        output_resolution = _validate_final_toolbar_options(run_id, mode, resolution)
        runtime = Path("output/216-runs") / run_id
        frames = (
            runtime
            / "final-frames-calendar-toolbar"
            / mode.directory_name
            / resolution_name(output_resolution)
        )
        sequence = inspect_final_frames(frames, output_resolution, frame_count=FRAME_COUNT)
        tools = detect_ffmpeg()
        final = _toolbar_final_directory(runtime, mode, output_resolution)
        output = final / "final-video-no-audio.mp4"
        command = build_final_visual_command(tools, frames, output, frame_count=FRAME_COUNT, fps=6)
        compose_final_visual(
            tools,
            frames,
            output,
            output_resolution,
            frame_count=FRAME_COUNT,
            fps=6,
        )
        probe = probe_final_visual(tools, output)
        validate_final_visual_probe(
            probe,
            output_resolution,
            expected_frame_count=FRAME_COUNT,
            expected_fps=6,
            expected_duration_seconds=36,
        )
        report = {
            "layout": "calendar-toolbar",
            "input_directory": str(frames),
            "count": sequence.count,
            "first": sequence.first.name,
            "last": sequence.last.name,
            "fps": 6,
            "duration_seconds": probe.duration_seconds,
            "resolution": list(output_resolution),
            "ffmpeg_command": command,
            "output": str(output),
            "validation": "PASS",
            "browser_opened": False,
            "google_calendar_reads": False,
            "google_calendar_writes": False,
        }
        write_atomic(final / "visual-composition-report.json", json.dumps(report, indent=2) + "\n")
    except (CalendarAnimError, OSError, RuntimeError, ValueError) as error:
        _fail(error)
    typer.echo("CAYDE 216 CALENDAR TOOLBAR VIDEO")
    typer.echo(f"Frames: {sequence.count}; {sequence.first.name} -> {sequence.last.name}")
    typer.echo("FPS/duration: 6 / 36.000000s")
    typer.echo(f"Visual MP4: {output}")
    typer.echo("Audio muxed: NO")
    typer.echo("Google Calendar reads/writes: NO")


def mux_final_cayde_216_calendar_toolbar_audio_command(
    run_id: Annotated[str, typer.Option("--run-id")] = RUN_ID,
    mode: Annotated[HybridOutputMode, typer.Option("--mode")] = (
        HybridOutputMode.HEADER_PRESERVED_FILL
    ),
    resolution: Annotated[str, typer.Option("--resolution")] = "1512x864",
    source_video: Annotated[Path, typer.Option("--source-video")] = Path("input.mp4"),
) -> None:
    """Mux the exact 114-150s audio clip into the Calendar-toolbar video."""

    try:
        output_resolution = _validate_final_toolbar_options(run_id, mode, resolution)
        if not source_video.is_file():
            raise CalendarAnimError(f"Source video does not exist: {source_video}")
        runtime = Path("output/216-runs") / run_id
        final = _toolbar_final_directory(runtime, mode, output_resolution)
        visual = final / "final-video-no-audio.mp4"
        if not visual.is_file():
            raise CalendarAnimError(f"Calendar-toolbar silent MP4 does not exist: {visual}")
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
            expected_video_frame_count=FRAME_COUNT,
        )
        report = {
            "layout": "calendar-toolbar",
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
            "browser_opened": False,
            "google_calendar_reads": False,
            "google_calendar_writes": False,
        }
        write_atomic(final / "audio-mux-report.json", json.dumps(report, indent=2) + "\n")
    except (CalendarAnimError, OSError, RuntimeError, ValueError) as error:
        _fail(error)
    typer.echo("CAYDE 216 CALENDAR TOOLBAR AUDIO")
    typer.echo(f"Final with audio: {output}")
    typer.echo(f"A/V delta: {probe.av_delta_seconds:.6f}s")
    typer.echo("Video copied: YES")
    typer.echo("Google Calendar reads/writes: NO")


def _validate_final_toolbar_options(
    run_id: str,
    mode: HybridOutputMode,
    resolution: str,
) -> tuple[int, int]:
    if run_id != RUN_ID or mode is not HybridOutputMode.HEADER_PRESERVED_FILL:
        raise CalendarAnimError("Calendar-toolbar media is locked to its run and final mode")
    output_resolution = parse_output_resolution(resolution)
    if output_resolution != CAPTURE_RESOLUTION:
        raise CalendarAnimError("Calendar-toolbar media requires resolution 1512x864")
    return output_resolution


def _toolbar_final_directory(
    runtime: Path,
    mode: HybridOutputMode,
    resolution: tuple[int, int],
) -> Path:
    return (
        runtime / "final" / "calendar-toolbar" / mode.directory_name / resolution_name(resolution)
    )


def _build_before_after(before: Path, after: Path, output: Path) -> Path:
    tile_size = (756, 432)
    label_height = 32
    sheet = Image.new("RGB", (1512, 464), "#101214")
    draw = ImageDraw.Draw(sheet)
    draw.text((12, 10), "ANTES | header_preserved_fill", fill="white")
    draw.text((768, 10), "DEPOIS | Calendar toolbar preservada", fill="white")
    with Image.open(before) as opened:
        left = opened.convert("RGB").resize(tile_size, Image.Resampling.LANCZOS)
    with Image.open(after) as opened:
        right = opened.convert("RGB").resize(tile_size, Image.Resampling.LANCZOS)
    sheet.paste(left, (0, label_height))
    sheet.paste(right, (756, label_height))
    output.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(output)
    left.close()
    right.close()
    sheet.close()
    return output


def _preview_report_text(report: dict[str, object]) -> str:
    composition = report["composition"]
    artifacts = report["artifacts"]
    changes = report["changes_from_previous"]
    assert isinstance(composition, dict)
    assert isinstance(artifacts, dict)
    assert isinstance(changes, dict)
    return "\n".join(
        [
            "CAYDE 216 CALENDAR TOOLBAR PREVIEW",
            "==================================",
            "",
            f"Frame: {report['human_frame']}",
            f"Week: {report['week_start']}",
            f"Native composite: {composition['native_composite_dimensions']}",
            f"Final dimensions: {composition['output_dimensions']}",
            f"Previous grid output height: {changes['previous_output_grid_height']}",
            f"New grid output height: {changes['new_output_grid_height']}",
            f"Preview: {artifacts['preview']}",
            f"Comparison: {artifacts['comparison']}",
            "Browser chrome included: NO",
            "OS UI included: NO",
            "Create button included: NO",
            "Right addon sidebar included: NO",
            "Protected artifacts changed: NO",
            "Capture checkpoint touched: NO",
            "Full capture started: NO",
            "Video composition started: NO",
            "Audio mux started: NO",
            "Google Calendar reads: NO",
            "Google Calendar writes: NO",
            "",
            f"Future command: {report['future_full_command']}",
            "",
        ]
    )
