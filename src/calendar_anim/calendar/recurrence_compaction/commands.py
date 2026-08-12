import re
from pathlib import Path
from typing import Annotated, Never

import typer

from calendar_anim.calendar.multi_frame.artifacts import AnimationRunStore
from calendar_anim.calendar.recurrence_compaction.artifacts import (
    write_recurrence_artifacts,
)
from calendar_anim.calendar.recurrence_compaction.planner import build_recurrence_study
from calendar_anim.exceptions import CalendarAnimError


def recurrence_plan_command(
    run_id: Annotated[str, typer.Option("--run-id")],
    output_root: Annotated[Path, typer.Option("--output-root")] = Path("output/animation-runs"),
    artifact_directory: Annotated[
        Path | None,
        typer.Option("--artifact-directory"),
    ] = None,
    parent_chunk_size: Annotated[
        int,
        typer.Option("--parent-chunk-size", min=1, max=730),
    ] = 100,
) -> None:
    """Build a local recurrence/RDATE compaction and migration study."""

    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", run_id):
        _fail(CalendarAnimError(f"Invalid run-id: {run_id!r}"))
    try:
        store = AnimationRunStore(output_root)
        plan = store.load_plan(run_id)
        state = store.load_state(run_id)
        result = build_recurrence_study(
            plan,
            state,
            store,
            migration_chunk_size=parent_chunk_size,
        )
        destination = artifact_directory or Path("output/recurrence-studies") / run_id
        plan_path, report_json_path, report_text_path = write_recurrence_artifacts(
            destination,
            result.migration_plan,
            result.report,
        )
    except (CalendarAnimError, OSError, ValueError) as error:
        _fail(error)

    typer.echo("RECURRENCE COMPACTION STUDY")
    typer.echo(f"Independent inserts: {result.report.current_independent_inserts}")
    typer.echo(f"Unique signatures: {result.report.unique_exact_signatures}")
    typer.echo("Parents (unlimited): " + str(result.report.full_scope.parents_unlimited))
    for chunk in result.report.chunk_sizes:
        typer.echo(f"Parents (chunk {chunk}): {result.report.full_scope.parents_by_chunk[chunk]}")
    typer.echo(f"Migration remaining: {result.report.remaining_occurrences}")
    typer.echo(f"Migration parents: {result.report.migration_parents_required}")
    typer.echo(
        "Expansion equals original: "
        + ("YES" if result.report.expanded_full_set_equals_original else "NO")
    )
    typer.echo(f"Plan: {plan_path}")
    typer.echo(f"JSON report: {report_json_path}")
    typer.echo(f"Text report: {report_text_path}")
    typer.echo("Google Calendar writes: NO")


def register_recurrence_compaction_commands(app: typer.Typer) -> None:
    app.command("recurrence-plan")(recurrence_plan_command)


def _fail(error: Exception) -> Never:
    typer.secho(f"Error: {error}", fg=typer.colors.RED, err=True)
    raise typer.Exit(code=1)
