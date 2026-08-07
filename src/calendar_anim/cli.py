import logging
import re
from datetime import date
from pathlib import Path
from typing import Annotated, Any

import typer
from pydantic import ValidationError

from calendar_anim.calendar.dry_run import DryRunCalendarGateway
from calendar_anim.calendar.mapper import plan_events
from calendar_anim.config import RenderConfig, config_from_yaml
from calendar_anim.exceptions import CalendarAnimError
from calendar_anim.pipeline import render_video
from calendar_anim.renderer.manifest import read_manifest, validate_manifest_files
from calendar_anim.video.inspector import inspect_video

app = typer.Typer(
    help="Create pixel-art animation manifests from video clips.", no_args_is_help=True
)
calendar_app = typer.Typer(help="Plan safe, local calendar operations.", no_args_is_help=True)
app.add_typer(calendar_app, name="calendar")


@app.callback()
def main(
    verbose: Annotated[
        bool, typer.Option("--verbose", "-v", help="Enable informational logs.")
    ] = False,
    debug: Annotated[
        bool, typer.Option("--debug", help="Enable debug logs and tracebacks.")
    ] = False,
) -> None:
    level = logging.DEBUG if debug else logging.INFO if verbose else logging.WARNING
    logging.basicConfig(level=level, format="%(levelname)s: %(message)s")


def _abort(error: Exception) -> None:
    typer.secho(f"Error: {error}", fg=typer.colors.RED, err=True)
    raise typer.Exit(code=1)


@app.command("inspect")
def inspect_command(video: Annotated[Path, typer.Argument(help="Input video path.")]) -> None:
    """Inspect video metadata without processing the full file."""
    try:
        info = inspect_video(video)
    except CalendarAnimError as error:
        _abort(error)
    typer.echo(f"Path: {info.path}")
    typer.echo(f"Format: {info.extension}")
    typer.echo(f"Codec: {info.codec or 'unknown'}")
    typer.echo(f"Dimensions: {info.width}x{info.height}")
    typer.echo(f"FPS: {info.fps:.3f}")
    typer.echo(f"Frames: {info.total_frames}")
    typer.echo(f"Duration: {info.duration_seconds:.3f} s")
    typer.echo("Audio: not inspected (audio is ignored)")
    for warning in info.warnings:
        typer.secho(f"Warning: {warning}", fg=typer.colors.YELLOW)


def _safe_id(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9-]+", "-", value.lower()).strip("-")
    return (normalized or "animation")[:63]


def _render_config(
    output: Path,
    config_path: Path | None,
    overrides: dict[str, Any],
    crop_overrides: dict[str, int | None],
) -> RenderConfig:
    config = (
        config_from_yaml(config_path)
        if config_path
        else RenderConfig(animation_id=_safe_id(output.name))
    )
    data = config.model_dump()
    data.update({key: value for key, value in overrides.items() if value is not None})
    crop = config.crop.model_dump()
    crop.update({key: value for key, value in crop_overrides.items() if value is not None})
    data["crop"] = crop
    return RenderConfig.model_validate(data)


@app.command("render")
def render_command(
    video: Annotated[Path, typer.Argument(help="Input video path.")],
    output: Annotated[Path, typer.Option("--output", "-o", help="Output directory.")] = Path(
        "output/animation"
    ),
    config: Annotated[
        Path | None, typer.Option("--config", help="Optional YAML configuration.")
    ] = None,
    animation_id: Annotated[str | None, typer.Option("--animation-id")] = None,
    start: Annotated[float | None, typer.Option("--start")] = None,
    duration: Annotated[float | None, typer.Option("--duration")] = None,
    frames: Annotated[int | None, typer.Option("--frames")] = None,
    width: Annotated[int | None, typer.Option("--width")] = None,
    height: Annotated[int | None, typer.Option("--height")] = None,
    colors: Annotated[int | None, typer.Option("--colors")] = None,
    palette: Annotated[str | None, typer.Option("--palette")] = None,
    background: Annotated[str | None, typer.Option("--background")] = None,
    background_tolerance: Annotated[float | None, typer.Option("--background-tolerance")] = None,
    output_fps: Annotated[float | None, typer.Option("--output-fps")] = None,
    fit: Annotated[str | None, typer.Option("--fit")] = None,
    crop_x: Annotated[int | None, typer.Option("--crop-x")] = None,
    crop_y: Annotated[int | None, typer.Option("--crop-y")] = None,
    crop_width: Annotated[int | None, typer.Option("--crop-width")] = None,
    crop_height: Annotated[int | None, typer.Option("--crop-height")] = None,
) -> None:
    """Render processed frames, preview GIF, and versioned manifest."""
    try:
        render_config = _render_config(
            output,
            config,
            {
                "animation_id": animation_id,
                "start_seconds": start,
                "duration_seconds": duration,
                "frame_count": frames,
                "grid_width": width,
                "grid_height": height,
                "colors": colors,
                "palette": palette,
                "background": background,
                "background_tolerance": background_tolerance,
                "output_fps": output_fps,
                "fit": fit,
            },
            {"x": crop_x, "y": crop_y, "width": crop_width, "height": crop_height},
        )
        manifest, info, warnings = render_video(video, output, render_config)
    except (CalendarAnimError, ValidationError, ValueError) as error:
        _abort(error)
    typer.echo(f"Video: {info.path}")
    typer.echo(f"Duration: {manifest.source.duration_seconds:.2f} s")
    typer.echo(f"Source frames: {info.total_frames}")
    typer.echo(f"Selected frames: {manifest.render.frame_count}")
    typer.echo(f"Grid: {manifest.render.grid_width}x{manifest.render.grid_height}")
    typer.echo(f"Palette: {manifest.render.palette} ({manifest.render.colors} colors)")
    typer.echo(f"Background removal: {'enabled' if manifest.render.background else 'disabled'}")
    typer.echo(f"Blocks generated: {manifest.statistics.blocks}")
    typer.echo(f"Estimated Calendar events: {manifest.statistics.estimated_events}")
    typer.echo(f"Preview: {output / 'preview.gif'}")
    typer.echo(f"Manifest: {output / 'animation.json'}")
    for warning in warnings:
        typer.secho(f"Warning: {warning}", fg=typer.colors.YELLOW)


@app.command("estimate")
def estimate_command(
    manifest_path: Annotated[Path, typer.Argument(help="animation.json path.")],
) -> None:
    """Estimate the future Calendar footprint (one block equals one event)."""
    try:
        manifest = read_manifest(manifest_path)
    except CalendarAnimError as error:
        _abort(error)
    counts = [len(frame.blocks) for frame in manifest.frames]
    frame_count = len(counts)
    typer.echo(f"Frames: {frame_count}")
    typer.echo(f"Grid: {manifest.render.grid_width}x{manifest.render.grid_height}")
    typer.echo(f"Non-empty pixels: {manifest.statistics.non_empty_pixels}")
    typer.echo(f"Blocks / estimated events: {sum(counts)}")
    typer.echo(f"Average events per frame: {sum(counts) / frame_count:.2f}")
    typer.echo(f"Maximum events in one frame: {max(counts, default=0)}")
    typer.echo(f"Weeks used: {frame_count}")
    typer.echo(f"Approximate playback duration: {frame_count / manifest.render.output_fps:.2f} s")
    typer.echo("Estimate rule: 1 block = 1 event; 1 frame = 1 week (experimental).")


@app.command("validate")
def validate_command(
    manifest_path: Annotated[Path, typer.Argument(help="animation.json path.")],
) -> None:
    """Validate schema, frame paths, indices, statistics, and block bounds."""
    try:
        manifest = read_manifest(manifest_path)
        errors = validate_manifest_files(manifest, manifest_path.resolve())
    except CalendarAnimError as error:
        _abort(error)
    if errors:
        for validation_error in errors:
            typer.secho(f"Error: {validation_error}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)
    typer.secho("Manifest is valid.", fg=typer.colors.GREEN)


@calendar_app.command("plan")
def calendar_plan_command(
    manifest_path: Annotated[Path, typer.Argument(help="animation.json path.")],
    start_date_value: Annotated[
        str, typer.Option("--start-date", help="First frame week (YYYY-MM-DD).")
    ],
    timezone: Annotated[str, typer.Option("--timezone")] = "UTC",
    output: Annotated[Path, typer.Option("--output", "-o")] = Path("calendar-plan.json"),
) -> None:
    """Create a local dry-run plan; never contacts Google."""
    try:
        start_date = date.fromisoformat(start_date_value)
        manifest = read_manifest(manifest_path)
        plan = plan_events(manifest, start_date, timezone)
        output.parent.mkdir(parents=True, exist_ok=True)
        DryRunCalendarGateway.export(plan, output)
    except (CalendarAnimError, OSError) as error:
        _abort(error)
    typer.echo(f"Planned events: {len(plan.events)}")
    typer.echo(f"Weeks: {manifest.render.frame_count}")
    typer.echo(f"Dry-run plan: {output}")
