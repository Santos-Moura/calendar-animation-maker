from pathlib import Path
from typing import Annotated

import typer

from calendar_anim.calendar.calibration.profile import DEFAULT_PROFILE_PATH, load_profile
from calendar_anim.calendar.vertical_compression.artifacts import (
    build_vertical_compression_report,
    write_vertical_compression_artifacts,
)
from calendar_anim.calendar.vertical_compression.estimator import (
    estimate_manifest_vertical_compression,
)
from calendar_anim.exceptions import CalendarAnimError
from calendar_anim.renderer.manifest import read_manifest, validate_manifest_files


def estimate_compression_command(
    manifest_path: Annotated[Path, typer.Argument(help="animation.json path.")],
    calibration_profile: Annotated[
        Path, typer.Option("--calibration-profile", "--profile")
    ] = DEFAULT_PROFILE_PATH,
    calendar_background_color_id: Annotated[
        str | None,
        typer.Option(
            "--calendar-background-color-id",
            help="Calendar colorId used by full-grid structural background cells (default: 8).",
        ),
    ] = None,
    output: Annotated[Path | None, typer.Option("--output", "-o")] = None,
) -> None:
    """Estimate vertical run compression locally; never calls Google."""
    try:
        manifest = read_manifest(manifest_path)
        errors = validate_manifest_files(manifest, manifest_path.resolve())
        if errors:
            raise CalendarAnimError("Manifest validation failed: " + "; ".join(errors))
        profile = load_profile(calibration_profile)
        estimate = estimate_manifest_vertical_compression(
            manifest,
            profile,
            calendar_background_color_id=calendar_background_color_id,
        )
        output_dir = output or Path("output/compression-estimates") / manifest.animation_id
        report_path, json_path = write_vertical_compression_artifacts(estimate, output_dir)
    except (CalendarAnimError, OSError, ValueError) as error:
        typer.secho(f"Error: {error}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from error

    typer.echo(build_vertical_compression_report(estimate), nl=False)
    typer.echo(f"Report: {report_path}")
    typer.echo(f"JSON: {json_path}")
    typer.echo("No authentication or Calendar API call was made.")


def register_vertical_compression_commands(app: typer.Typer) -> None:
    app.command("estimate-compression")(estimate_compression_command)
