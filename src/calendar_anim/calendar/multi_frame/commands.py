import re
from datetime import date
from pathlib import Path
from typing import Annotated, Never

import typer
from googleapiclient.errors import HttpError

from calendar_anim.calendar.calibration.patterns import DEFAULT_CALENDAR_NAME
from calendar_anim.calendar.calibration.profile import DEFAULT_PROFILE_PATH, load_profile
from calendar_anim.calendar.frame_mapping.models import (
    DEFAULT_EVENT_COMPRESSION,
    EventCompressionMode,
    FitMode,
    FrameMappingMode,
)
from calendar_anim.calendar.frame_mapping.service import (
    ABSOLUTE_SINGLE_FRAME_MAX_EVENTS,
    DEFAULT_SINGLE_FRAME_MAX_EVENTS,
)
from calendar_anim.calendar.google_auth import GoogleOAuthClient
from calendar_anim.calendar.google_gateway import GoogleCalendarGateway
from calendar_anim.calendar.high_detail import (
    HIGH_DETAIL_GRID,
    HIGH_DETAIL_GRID_PROFILE,
    apply_high_detail_grid,
    high_detail_max_events_for_run,
)
from calendar_anim.calendar.lab import LabCalendarService
from calendar_anim.calendar.local_config import CalendarConfigStore
from calendar_anim.calendar.multi_frame.artifacts import (
    AnimationRunStore,
    initialize_animation_run,
)
from calendar_anim.calendar.multi_frame.cleanup import MultiFrameCleanupService
from calendar_anim.calendar.multi_frame.models import (
    AnimationUploadState,
    FrameUploadState,
    FrameUploadStatus,
    MultiFramePlan,
)
from calendar_anim.calendar.multi_frame.performance import FrameUploadPerformance
from calendar_anim.calendar.multi_frame.planner import build_multi_frame_plan
from calendar_anim.calendar.multi_frame.service import MultiFrameUploadService
from calendar_anim.calendar.subcolumn_ordering import (
    SubcolumnOrderStrategy,
    format_summary_key,
)
from calendar_anim.exceptions import CalendarAnimError
from calendar_anim.renderer.manifest import read_manifest, validate_manifest_files


def _fail(error: Exception) -> Never:
    typer.secho(f"Error: {error}", fg=typer.colors.RED, err=True)
    raise typer.Exit(code=1)


def _google_gateway() -> GoogleCalendarGateway:
    return GoogleCalendarGateway(GoogleOAuthClient().build_service())


def _valid_run_id(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", value):
        raise CalendarAnimError(f"Invalid run-id: {value!r}")
    return value


def plan_animation_command(
    manifest_path: Annotated[Path, typer.Argument(help="animation.json path.")],
    start_date_value: Annotated[
        str, typer.Option("--start-date", help="Any date in the first selected week.")
    ],
    run_id: Annotated[str, typer.Option("--run-id")],
    frame_start: Annotated[int, typer.Option("--frame-start", min=0)] = 0,
    frame_count: Annotated[int, typer.Option("--frame-count", min=1)] = 1,
    calibration_profile: Annotated[
        Path, typer.Option("--calibration-profile", "--profile")
    ] = DEFAULT_PROFILE_PATH,
    experimental_grid: Annotated[
        str | None,
        typer.Option(
            "--experimental-grid",
            help="Explicit high-detail grid override; production defaults remain unchanged.",
        ),
    ] = None,
    output_root: Annotated[Path, typer.Option("--output-root")] = Path("output/animation-runs"),
    mapping_mode: Annotated[
        FrameMappingMode, typer.Option("--mapping-mode")
    ] = FrameMappingMode.FULL_GRID,
    event_compression: Annotated[
        EventCompressionMode,
        typer.Option(
            "--event-compression",
            help=(
                "Calendar event compression strategy. The production default is "
                "synchronized-horizontal-bands; use none for baseline/debug behavior."
            ),
        ),
    ] = DEFAULT_EVENT_COMPRESSION,
    calendar_background_color_id: Annotated[
        str | None, typer.Option("--calendar-background-color-id")
    ] = None,
    palette_preset: Annotated[
        str | None,
        typer.Option(
            "--palette-preset",
            help="Lock an approved artistic Calendar color mapping (for example cayde-final).",
        ),
    ] = None,
    subcolumn_ordering: Annotated[
        SubcolumnOrderStrategy | None,
        typer.Option(
            "--subcolumn-ordering",
            help=(
                "Summary ordering for new full-grid plans. Default: zero-width; "
                "use numeric for visible debug/baseline summaries."
            ),
        ),
    ] = None,
    max_events: Annotated[int, typer.Option("--max-events", min=1)] = (
        DEFAULT_SINGLE_FRAME_MAX_EVENTS
    ),
    calendar_name: Annotated[str, typer.Option("--calendar-name")] = DEFAULT_CALENDAR_NAME,
) -> None:
    """Build immutable multi-frame plans and pending state using local files only."""
    try:
        resolved_run_id = _valid_run_id(run_id)
        allowed_max_events = (
            high_detail_max_events_for_run(resolved_run_id)
            if experimental_grid is not None
            and experimental_grid.lower().strip() == HIGH_DETAIL_GRID
            else ABSOLUTE_SINGLE_FRAME_MAX_EVENTS
        )
        if max_events > allowed_max_events:
            raise CalendarAnimError(
                f"--max-events cannot exceed the absolute safety limit of {allowed_max_events}"
            )
        manifest = read_manifest(manifest_path)
        errors = validate_manifest_files(manifest, manifest_path.resolve())
        if errors:
            raise CalendarAnimError("Manifest validation failed: " + "; ".join(errors))
        profile = load_profile(calibration_profile)
        if experimental_grid is not None:
            profile = apply_high_detail_grid(profile, experimental_grid)
        fit: FitMode = "contain"
        plan, frame_plans = build_multi_frame_plan(
            manifest,
            profile,
            frame_start=frame_start,
            frame_count=frame_count,
            anchor_date=date.fromisoformat(start_date_value),
            run_id=resolved_run_id,
            max_events_per_frame=max_events,
            fit=fit,
            calendar_name=calendar_name,
            mapping_mode=mapping_mode,
            event_compression=event_compression,
            calendar_background_color_id=calendar_background_color_id,
            palette_preset=palette_preset,
            subcolumn_order_strategy=subcolumn_ordering,
            grid_profile=(
                HIGH_DETAIL_GRID_PROFILE if experimental_grid is not None else "production"
            ),
        )
        store = AnimationRunStore(output_root)
        state = initialize_animation_run(plan, frame_plans, manifest, manifest_path, store)
    except (CalendarAnimError, OSError, ValueError) as error:
        _fail(error)
    typer.echo(f"Animation ID: {plan.animation_id}")
    typer.echo(f"Run ID: {plan.run_id}")
    typer.echo(f"Source: {plan.source_file}")
    typer.echo(
        f"Clip: {plan.clip_start_seconds:.3f}-{plan.clip_end_seconds:.3f} seconds, "
        f"{plan.clip_duration_seconds:.3f}s at {plan.output_fps:.3f} FPS"
    )
    typer.echo(f"Frames: {plan.frame_count}")
    typer.echo(f"Weeks: {plan.frame_count} ({plan.start_week} onward)")
    typer.echo(f"Mapping mode: {plan.mapping_mode.value}")
    typer.echo(f"Event compression: {plan.event_compression.value}")
    typer.echo(f"Palette preset: {plan.palette_preset or 'none'}")
    typer.echo(f"Background colorId: {plan.background_color_id or 'automatic'}")
    typer.echo(
        "Foreground colorIds: "
        + (", ".join(plan.foreground_color_ids) if plan.foreground_color_ids else "profile")
    )
    typer.echo(f"Target grid: {plan.target_grid_width}x{plan.target_grid_height}")
    typer.echo(f"Grid profile: {plan.grid_profile}")
    typer.echo(f"Slots/day: {plan.slots_per_day}")
    typer.echo(f"Vertical step: {plan.vertical_step_minutes} minutes")
    typer.echo(
        f"Visible window: {plan.visible_start_hour:02d}:00-"
        f"{'00:00' if plan.visible_end_hour == 24 else f'{plan.visible_end_hour:02d}:00'}"
    )
    typer.echo(f"Subcolumn ordering: {plan.subcolumn_order_strategy.value}")
    typer.echo(
        "Slot keys: "
        + (
            ", ".join(format_summary_key(key) for key in plan.subcolumn_order_keys)
            if plan.subcolumn_order_keys
            else "not used"
        )
    )
    typer.echo(f"Events/frame: {', '.join(str(value) for value in plan.events_per_frame)}")
    typer.echo(f"Max events/frame: {plan.max_events_per_frame}")
    typer.echo(f"Total events: {plan.total_events}")
    typer.echo(f"Mapper readiness: {'READY' if plan.profile_ready else 'NOT READY'}")
    typer.echo(f"Initial state: {_status_counts(state)}")
    typer.echo("Execution: DRY RUN")
    typer.echo(f"Artifacts: {store.run_directory(plan.run_id)}")
    typer.echo("This command used local files only; no Calendar API call was made.")


def upload_animation_command(
    run_id: Annotated[str, typer.Option("--run-id")],
    output_root: Annotated[Path, typer.Option("--output-root")] = Path("output/animation-runs"),
    resume: Annotated[
        bool, typer.Option("--resume", help="Explicitly acknowledge checkpointed progress.")
    ] = False,
    recover_partial: Annotated[
        bool,
        typer.Option(
            "--recover-partial",
            help=(
                "Compatibility flag; partial frames now recover automatically by deterministic "
                "event identity, with scoped cleanup only as a legacy fallback."
            ),
        ),
    ] = False,
    execute: Annotated[
        bool, typer.Option("--execute", help="Create real events in the lab calendar.")
    ] = False,
    yes: Annotated[bool, typer.Option("--yes", help="Skip confirmation with --execute.")] = False,
) -> None:
    """Inspect or serially upload a saved animation plan with frame checkpoints."""
    try:
        resolved_run_id = _valid_run_id(run_id)
        store = AnimationRunStore(output_root)
        plan = store.load_plan(resolved_run_id)
        state = store.load_state(resolved_run_id)
    except (CalendarAnimError, OSError, ValueError) as error:
        _fail(error)
    _print_upload_summary(plan, state, execute)
    if resume:
        typer.echo("Resume: enabled; completed frames will be skipped.")
    if not execute:
        _print_dry_run_actions(state)
        if recover_partial:
            typer.echo("--recover-partial has no effect without --execute.")
        if yes:
            typer.echo("--yes has no effect without --execute.")
        typer.echo("No authentication or Calendar API call was made.")
        return
    if not plan.profile_ready:
        _fail(CalendarAnimError("Mapper is NOT READY; real animation upload is blocked"))
    partial = [
        frame.frame_index
        for frame in state.frames
        if frame.status in {FrameUploadStatus.PARTIAL, FrameUploadStatus.UPLOADING}
    ]
    if not yes:
        typer.echo("\nThis operation may take a long time.")
        typer.echo("Completed frames will be checkpointed and skipped on resume.")
        if partial:
            typer.echo(
                "Automatic recovery will reconcile partial frame(s): "
                + ", ".join(str(index) for index in partial)
            )
        typer.confirm("Continue?", default=False, abort=True)
    positions = {frame.frame_index: position for position, frame in enumerate(plan.frames, start=1)}

    def progress(frame_index: int, created: int, planned: int) -> None:
        if created == 0:
            frame = next(item for item in plan.frames if item.frame_index == frame_index)
            typer.echo(
                f"\nFrame {positions[frame_index]}/{plan.frame_count} "
                f"(index {frame_index}, week {frame.week_start})"
            )
            typer.echo(f"Uploading: 0/{planned}")
        else:
            typer.echo(f"Uploading: {created}/{planned}")

    def frame_complete(performance: FrameUploadPerformance) -> None:
        finished_at = performance.finished_at.isoformat() if performance.finished_at else "pending"
        typer.echo(f"Completed: {finished_at}")
        typer.echo(f"Status: {performance.status.value}")
        typer.echo(f"Created: {performance.created_events}/{performance.planned_events}")
        typer.echo(f"Failed: {performance.failed_events}")
        typer.echo(f"Elapsed: {_seconds(performance.elapsed_seconds)}")
        typer.echo(f"Rate: {_rate(performance.events_per_second)}")
        typer.echo(f"Event retries: {performance.event_retry_count}")
        typer.echo(f"Recovery cycles: {performance.recovery_cycles}")

    try:
        gateway = _google_gateway()
        service = MultiFrameUploadService(
            gateway,
            LabCalendarService(gateway, CalendarConfigStore()),
            store,
            progress=progress,
            frame_complete=frame_complete,
        )
        state = service.upload(plan, state, recover_partial=recover_partial)
    except KeyboardInterrupt:
        typer.secho("Upload interrupted; the current frame was checkpointed as partial.", fg="red")
        raise typer.Exit(code=130) from None
    except (CalendarAnimError, HttpError, OSError) as error:
        typer.echo(f"Performance report: {store.performance_json_path(plan.run_id)}")
        _fail(error)
    typer.echo(f"\nAnimation progress: {_status_counts(state)}")
    for frame in state.frames:
        duration = "pending" if frame.duration_seconds is None else f"{frame.duration_seconds:.2f}s"
        typer.echo(
            f"Frame {frame.frame_index}: {frame.status.value}, "
            f"{frame.created_events}/{frame.planned_events}, duration {duration}"
        )
    performance = store.load_performance(plan.run_id)
    invocation = performance.invocations[-1]
    typer.echo(
        "Frames uploaded this invocation: "
        + _frame_indexes(invocation.frames_uploaded_this_invocation)
    )
    typer.echo(
        "Frames previously completed: " + _frame_indexes(invocation.frames_previously_completed)
    )
    typer.echo(f"Created: {performance.total_created_events}")
    typer.echo(f"Failed: {performance.total_failed_events}")
    typer.echo(f"Total elapsed: {_seconds(performance.total_elapsed_seconds)}")
    typer.echo(f"Average/frame: {_seconds(performance.average_seconds_per_frame)}")
    typer.echo(f"Overall events/sec: {_rate(performance.overall_events_per_second)}")
    typer.echo(f"Performance report: {store.performance_json_path(plan.run_id)}")
    if any(
        frame.status in {FrameUploadStatus.PARTIAL, FrameUploadStatus.FAILED}
        for frame in state.frames
    ):
        raise typer.Exit(code=1)


def cleanup_animation_command(
    run_id: Annotated[str, typer.Option("--run-id")],
    frame_index: Annotated[int | None, typer.Option("--frame", min=0)] = None,
    output_root: Annotated[Path, typer.Option("--output-root")] = Path("output/animation-runs"),
    execute: Annotated[
        bool, typer.Option("--execute", help="Delete matching real events.")
    ] = False,
    yes: Annotated[bool, typer.Option("--yes", help="Skip confirmation with --execute.")] = False,
) -> None:
    """Delete one frame or all frames from a saved animation run."""
    try:
        store = AnimationRunStore(output_root)
        plan = store.load_plan(_valid_run_id(run_id))
        state = store.load_state(plan.run_id)
        selected = _selected_states(plan, state, frame_index)
    except (CalendarAnimError, OSError, ValueError) as error:
        _fail(error)
    typer.echo(f"Animation ID: {plan.animation_id}")
    typer.echo(f"Run ID: {plan.run_id}")
    typer.echo(
        "Frames: " + (str(frame_index) if frame_index is not None else f"all ({plan.frame_count})")
    )
    if not execute:
        typer.echo(f"Recorded created events: {sum(frame.created_events for frame in selected)}")
        typer.echo("Execution: DRY RUN")
        typer.echo("No authentication, Calendar lookup, or deletion was performed.")
        if yes:
            typer.echo("--yes has no effect without --execute.")
        return
    try:
        gateway = _google_gateway()
        service = MultiFrameCleanupService(
            LabCalendarService(gateway, CalendarConfigStore()), store
        )
        match = service.find_matches(plan, frame_index)
        typer.echo(f"Matching events: {match.event_count}")
        typer.echo("Execution: REAL")
        if not match.event_count:
            typer.echo("No matching events; nothing was changed.")
            return
        if not yes:
            typer.confirm("Delete only these matching animation events?", default=False, abort=True)
        result = service.cleanup(plan, state, match)
    except (CalendarAnimError, HttpError, OSError) as error:
        _fail(error)
    typer.echo(f"Deleted events: {result.deleted_events}")
    typer.echo(f"Failed deletions: {result.failed_events}")
    if result.failed_events:
        raise typer.Exit(code=1)


def _print_upload_summary(plan: MultiFramePlan, state: AnimationUploadState, execute: bool) -> None:
    typer.echo(f"Calendar: {plan.calendar_name}")
    typer.echo(f"Run: {plan.run_id}")
    if plan.source_file is not None:
        typer.echo(f"Source: {plan.source_file}")
    if plan.clip_start_seconds is not None and plan.clip_end_seconds is not None:
        typer.echo(f"Clip: {plan.clip_start_seconds:.3f}-{plan.clip_end_seconds:.3f} seconds")
    typer.echo(f"Frames: {plan.frame_count}")
    typer.echo(f"Weeks: {plan.frame_count}")
    typer.echo(f"Grid: {plan.target_grid_width}x{plan.target_grid_height}")
    typer.echo(f"Mapping mode: {plan.mapping_mode.value}")
    typer.echo(f"Event compression: {plan.event_compression.value}")
    typer.echo(f"Summary ordering: {plan.subcolumn_order_strategy.value}")
    typer.echo(f"Events/frame: {', '.join(str(value) for value in plan.events_per_frame)}")
    typer.echo(f"Total planned events: {plan.total_events}")
    typer.echo(f"Max events/frame: {plan.max_events_per_frame}")
    typer.echo(f"Largest frame: {max(plan.events_per_frame)}")
    typer.echo(f"Current state: {_status_counts(state)}")
    typer.echo(f"Execution: {'REAL' if execute else 'DRY RUN'}")


def _print_dry_run_actions(state: AnimationUploadState) -> None:
    typer.echo("\nPlanned actions:")
    for frame in state.frames:
        if frame.status is FrameUploadStatus.COMPLETED:
            action = "SKIP (completed)"
        elif frame.status in {FrameUploadStatus.PARTIAL, FrameUploadStatus.UPLOADING}:
            action = "AUTO-RECOVER MISSING EVENTS"
        elif frame.status is FrameUploadStatus.FAILED:
            action = "AUTO-RECOVER, STOP SAFELY IF PERSISTENT"
        else:
            action = "UPLOAD"
        typer.echo(f"Frame {frame.frame_index}: {action}")


def _status_counts(state: AnimationUploadState) -> str:
    completed = sum(frame.status is FrameUploadStatus.COMPLETED for frame in state.frames)
    partial = sum(frame.status is FrameUploadStatus.PARTIAL for frame in state.frames)
    failed = sum(frame.status is FrameUploadStatus.FAILED for frame in state.frames)
    pending = len(state.frames) - completed - partial - failed
    return (
        f"{completed}/{len(state.frames)} completed, {pending} pending, "
        f"{partial} partial, {failed} failed"
    )


def _selected_states(
    plan: MultiFramePlan,
    state: AnimationUploadState,
    frame_index: int | None,
) -> list[FrameUploadState]:
    if frame_index is None:
        return state.frames
    if not any(frame.frame_index == frame_index for frame in plan.frames):
        raise CalendarAnimError(f"Animation plan has no frame {frame_index}")
    return [state.frame(frame_index)]


def _seconds(value: float | None) -> str:
    return "pending" if value is None else f"{value:.2f}s"


def _rate(value: float | None) -> str:
    return "pending" if value is None else f"{value:.2f} events/s"


def _frame_indexes(values: list[int]) -> str:
    return ", ".join(str(value) for value in values) if values else "none"


def register_multi_frame_commands(app: typer.Typer) -> None:
    app.command("plan-animation")(plan_animation_command)
    app.command("upload-animation")(upload_animation_command)
    app.command("cleanup-animation")(cleanup_animation_command)
