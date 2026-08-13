import json
import re
from pathlib import Path
from typing import Annotated, Never

import typer

from calendar_anim.calendar.multi_frame.artifacts import AnimationRunStore
from calendar_anim.calendar.recurrence_compaction.account_b_prefix import (
    ACCOUNT_B_PREFIX_RUN_ID,
    build_account_b_prefix_artifacts,
    save_account_b_prefix_artifacts,
    validate_prefix_input_hash,
)
from calendar_anim.calendar.recurrence_compaction.artifacts import (
    write_recurrence_artifacts,
)
from calendar_anim.calendar.recurrence_compaction.hybrid import (
    FINAL_HYBRID_RUN_ID,
    FINAL_SOURCE_RUN_ID,
    build_hybrid_final_artifacts,
    save_hybrid_artifacts,
    validate_input_hash,
)
from calendar_anim.calendar.recurrence_compaction.planner import build_recurrence_study
from calendar_anim.calendar.recurrence_upload.artifacts import RecurrenceUploadStore, file_sha256
from calendar_anim.calendar.recurrence_validation.ordering import OrderingCaptureResult
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
    app.command("prepare-final-hybrid-recurrence")(prepare_final_hybrid_recurrence_command)
    app.command("prepare-account-b-prefix-recurrence")(prepare_account_b_prefix_recurrence_command)


def prepare_account_b_prefix_recurrence_command(
    run_id: Annotated[str, typer.Option("--run-id")] = ACCOUNT_B_PREFIX_RUN_ID,
    source_run_id: Annotated[str, typer.Option("--source-run-id")] = FINAL_SOURCE_RUN_ID,
    input_file: Annotated[Path, typer.Option("--input")] = Path("input.mp4"),
    animation_output_root: Annotated[Path, typer.Option("--animation-output-root")] = Path(
        "output/animation-runs"
    ),
    existing_plan_root: Annotated[Path, typer.Option("--existing-plan-root")] = Path(
        "output/hybrid-plans"
    ),
    output_directory: Annotated[Path | None, typer.Option("--output-directory")] = None,
    parent_chunk_size: Annotated[int, typer.Option("--parent-chunk-size", min=1, max=730)] = 100,
) -> None:
    """Build the isolated Account-B frames 1-23 recurrence prefix locally."""

    try:
        if run_id != ACCOUNT_B_PREFIX_RUN_ID:
            raise CalendarAnimError(
                "Account-B prefix preparation requires the locked prefix run ID"
            )
        if parent_chunk_size != 100:
            raise CalendarAnimError("Account-B prefix recurrence requires chunk size 100")
        validate_prefix_input_hash(input_file)
        existing_store = RecurrenceUploadStore(existing_plan_root, Path("output/hybrid-runs"))
        existing_plan = existing_store.load_plan(FINAL_HYBRID_RUN_ID)
        existing_plan_path = (
            existing_plan_root / FINAL_HYBRID_RUN_ID / "account-b-recurrence-plan.json"
        )
        existing_hash_before = file_sha256(existing_plan_path)
        source_store = AnimationRunStore(animation_output_root)
        animation, recurrence, report, final = build_account_b_prefix_artifacts(
            source_store,
            existing_plan,
            source_run_id=source_run_id,
            run_id=run_id,
            chunk_size=parent_chunk_size,
            existing_b_plan_sha256=existing_hash_before,
        )
        if file_sha256(existing_plan_path) != existing_hash_before:
            raise CalendarAnimError("Existing Account-B recurrence plan changed during preparation")
        destination = output_directory or Path("output/account-b-prefix-plans") / run_id
        paths = save_account_b_prefix_artifacts(destination, animation, recurrence, report, final)
    except (CalendarAnimError, OSError, ValueError, json.JSONDecodeError) as error:
        _fail(error)
    typer.echo("ACCOUNT-B FULL ANIMATION PREFIX PLAN")
    typer.echo("Existing B frames 24-108: UNTOUCHED")
    typer.echo(f"Prefix frames: 1-23 ({final.prefix_first_week} -> {final.prefix_last_week})")
    typer.echo(f"Logical occurrences: {final.logical_occurrences}")
    typer.echo(f"Unique signatures: {final.unique_recurrence_signatures}")
    typer.echo(f"Parents chunk100: {final.recurring_parents}")
    typer.echo(f"Reduction: {final.reduction_percent:.3f}%")
    typer.echo(f"PREFIX/EXISTING-B WEEK OVERLAP: {final.prefix_existing_week_overlap}")
    typer.echo(f"PARENT ID COLLISIONS: {final.parent_id_collisions}")
    typer.echo(f"EXPANSION EQUALITY: {'YES' if final.expansion_exact else 'NO'}")
    for path in paths:
        typer.echo(f"Artifact: {path}")
    typer.echo("Account A reads: NO")
    typer.echo("Google Calendar reads: NO")
    typer.echo("Google Calendar writes: NO")


def prepare_final_hybrid_recurrence_command(
    ordering_validation_id: Annotated[
        str, typer.Option("--ordering-validation-id")
    ] = "recurrence-zero-width-ordering-account-b-01",
    source_run_id: Annotated[str, typer.Option("--source-run-id")] = FINAL_SOURCE_RUN_ID,
    run_id: Annotated[str, typer.Option("--run-id")] = FINAL_HYBRID_RUN_ID,
    input_file: Annotated[Path, typer.Option("--input")] = Path("input.mp4"),
    animation_output_root: Annotated[Path, typer.Option("--animation-output-root")] = Path(
        "output/animation-runs"
    ),
    validation_output_root: Annotated[Path, typer.Option("--validation-output-root")] = Path(
        "output/recurrence-validation"
    ),
    output_directory: Annotated[Path | None, typer.Option("--output-directory")] = None,
    parent_chunk_size: Annotated[int, typer.Option("--parent-chunk-size", min=1, max=730)] = 100,
) -> None:
    """Build the local A/B hybrid plan only after a persisted ordering PASS."""

    try:
        result_path = (
            validation_output_root / ordering_validation_id / "captures" / "ordering-result.json"
        )
        if not result_path.is_file():
            raise CalendarAnimError(
                "Final hybrid plan is blocked until the ordering validation is captured "
                "and produces ordering-result.json"
            )
        ordering = OrderingCaptureResult.model_validate_json(
            result_path.read_text(encoding="utf-8")
        )
        validate_input_hash(input_file)
        store = AnimationRunStore(animation_output_root)
        b_plan, recurrence, recurrence_report, hybrid = build_hybrid_final_artifacts(
            store,
            ordering,
            source_run_id=source_run_id,
            run_id=run_id,
            chunk_size=parent_chunk_size,
        )
        destination = output_directory or Path("output/hybrid-plans") / run_id
        paths = save_hybrid_artifacts(destination, b_plan, recurrence, recurrence_report, hybrid)
    except (CalendarAnimError, OSError, ValueError, json.JSONDecodeError) as error:
        _fail(error)
    typer.echo(f"Hybrid run: {hybrid.run_id}")
    typer.echo("Frames A: 0-22 (existing singles; untouched)")
    typer.echo("Frames B: 23-107 (full frames; recurrence/RDATE)")
    typer.echo(f"B occurrences: {hybrid.logical_occurrences_b}")
    typer.echo(f"B recurring parents (chunk {parent_chunk_size}): {hybrid.recurring_parents_b}")
    typer.echo(f"Reduction: {hybrid.api_reduction_percent:.3f}%")
    for path in paths:
        typer.echo(f"Artifact: {path}")
    typer.echo("Google Calendar writes: NO")


def _fail(error: Exception) -> Never:
    typer.secho(f"Error: {error}", fg=typer.colors.RED, err=True)
    raise typer.Exit(code=1)
