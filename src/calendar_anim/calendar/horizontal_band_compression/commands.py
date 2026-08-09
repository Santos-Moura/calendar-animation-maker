from pathlib import Path
from typing import Annotated

import typer

from calendar_anim.calendar.calibration.profile import DEFAULT_PROFILE_PATH, load_profile
from calendar_anim.calendar.horizontal_band_compression.artifacts import (
    build_horizontal_band_report,
    write_horizontal_band_artifacts,
)
from calendar_anim.calendar.horizontal_band_compression.estimator import (
    estimate_manifest_horizontal_bands,
)
from calendar_anim.exceptions import CalendarAnimError
from calendar_anim.renderer.manifest import read_manifest, validate_manifest_files


def estimate_band_compression_command(
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
    """Estimate synchronized horizontal bands locally; never calls Google."""
    try:
        manifest = read_manifest(manifest_path)
        errors = validate_manifest_files(manifest, manifest_path.resolve())
        if errors:
            raise CalendarAnimError("Manifest validation failed: " + "; ".join(errors))
        profile = load_profile(calibration_profile)
        estimate = estimate_manifest_horizontal_bands(
            manifest,
            profile,
            calendar_background_color_id=calendar_background_color_id,
        )
        output_dir = (
            output
            or Path("output/compression-estimates") / manifest.animation_id / "synchronized-bands"
        )
        report_path, json_path = write_horizontal_band_artifacts(estimate, output_dir)
    except (CalendarAnimError, OSError, ValueError) as error:
        typer.secho(f"Error: {error}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from error

    typer.echo(build_horizontal_band_report(estimate), nl=False)
    typer.echo(f"Report: {report_path}")
    typer.echo(f"JSON: {json_path}")
    typer.echo("No authentication or Calendar API call was made.")


def register_horizontal_band_compression_commands(app: typer.Typer) -> None:
    app.command("estimate-band-compression")(estimate_band_compression_command)
