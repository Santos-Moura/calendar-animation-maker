import os
from datetime import UTC, datetime
from pathlib import Path

from pydantic import ValidationError

from calendar_anim.calendar.frame_mapping.artifacts import write_frame_mapping_artifacts
from calendar_anim.calendar.frame_mapping.models import SingleFrameCalendarPlan
from calendar_anim.calendar.multi_frame.models import (
    AnimationUploadState,
    FrameUploadExecutionResult,
    FrameUploadState,
    FrameUploadStatus,
    MultiFramePlan,
)
from calendar_anim.calendar.multi_frame.performance import (
    UploadPerformanceReport,
    build_performance_text,
    initial_performance_report,
)
from calendar_anim.calendar.subcolumn_ordering import format_summary_key
from calendar_anim.exceptions import CalendarAnimError
from calendar_anim.models.animation import AnimationManifest


def _write_json_atomic(path: Path, serialized: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(serialized)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def initial_upload_state(plan: MultiFramePlan) -> AnimationUploadState:
    return AnimationUploadState(
        run_id=plan.run_id,
        animation_id=plan.animation_id,
        frames=[
            FrameUploadState(
                frame_index=frame.frame_index,
                planned_events=frame.planned_events,
            )
            for frame in plan.frames
        ],
        updated_at=datetime.now(UTC),
    )


class AnimationRunStore:
    def __init__(self, root: Path = Path("output/animation-runs")) -> None:
        self.root = root

    def run_directory(self, run_id: str) -> Path:
        return self.root / run_id

    def plan_path(self, run_id: str) -> Path:
        return self.run_directory(run_id) / "animation-plan.json"

    def state_path(self, run_id: str) -> Path:
        return self.run_directory(run_id) / "animation-state.json"

    def report_path(self, run_id: str) -> Path:
        return self.run_directory(run_id) / "animation-report.txt"

    def performance_json_path(self, run_id: str) -> Path:
        return self.run_directory(run_id) / "upload-performance.json"

    def performance_text_path(self, run_id: str) -> Path:
        return self.run_directory(run_id) / "upload-performance.txt"

    def save_plan(self, plan: MultiFramePlan) -> Path:
        path = self.plan_path(plan.run_id)
        if path.exists():
            existing = self.load_plan(plan.run_id)
            if existing != plan:
                raise CalendarAnimError(
                    f"Animation plan already exists with different content: {path}"
                )
            return path
        _write_json_atomic(path, plan.model_dump_json(indent=2) + "\n")
        return path

    def load_plan(self, run_id: str) -> MultiFramePlan:
        path = self.plan_path(run_id)
        try:
            return MultiFramePlan.model_validate_json(path.read_text(encoding="utf-8"))
        except FileNotFoundError as error:
            raise CalendarAnimError(f"Animation run does not exist: {run_id}") from error
        except (OSError, ValidationError, ValueError) as error:
            raise CalendarAnimError(f"Invalid animation plan: {path}") from error

    def save_state(self, state: AnimationUploadState) -> Path:
        state.updated_at = datetime.now(UTC)
        path = self.state_path(state.run_id)
        _write_json_atomic(path, state.model_dump_json(indent=2) + "\n")
        return path

    def load_state(self, run_id: str) -> AnimationUploadState:
        path = self.state_path(run_id)
        try:
            return AnimationUploadState.model_validate_json(path.read_text(encoding="utf-8"))
        except FileNotFoundError as error:
            raise CalendarAnimError(f"Animation state does not exist: {run_id}") from error
        except (OSError, ValidationError, ValueError) as error:
            raise CalendarAnimError(f"Invalid animation state: {path}") from error

    def save_report(self, plan: MultiFramePlan, state: AnimationUploadState) -> Path:
        path = self.report_path(plan.run_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(build_animation_report(plan, state), encoding="utf-8")
        return path

    def save_performance(self, report: UploadPerformanceReport) -> tuple[Path, Path]:
        json_path = self.performance_json_path(report.run_id)
        text_path = self.performance_text_path(report.run_id)
        _write_json_atomic(json_path, report.model_dump_json(indent=2) + "\n")
        text_path.write_text(build_performance_text(report), encoding="utf-8")
        return json_path, text_path

    def load_performance(self, run_id: str) -> UploadPerformanceReport:
        path = self.performance_json_path(run_id)
        try:
            return UploadPerformanceReport.model_validate_json(path.read_text(encoding="utf-8"))
        except FileNotFoundError as error:
            raise CalendarAnimError(
                f"Upload performance report does not exist: {run_id}"
            ) from error
        except (OSError, ValidationError, ValueError) as error:
            raise CalendarAnimError(f"Invalid upload performance report: {path}") from error

    def frame_directory(self, plan: MultiFramePlan, frame_index: int) -> Path:
        frame = next((item for item in plan.frames if item.frame_index == frame_index), None)
        if frame is None:
            raise CalendarAnimError(f"Animation plan has no frame {frame_index}")
        return self.run_directory(plan.run_id) / frame.artifact_directory

    def load_frame_plan(self, plan: MultiFramePlan, frame_index: int) -> SingleFrameCalendarPlan:
        path = self.frame_directory(plan, frame_index) / "frame-plan.json"
        try:
            return SingleFrameCalendarPlan.model_validate_json(path.read_text(encoding="utf-8"))
        except FileNotFoundError as error:
            raise CalendarAnimError(f"Frame plan does not exist: {path}") from error
        except (OSError, ValidationError, ValueError) as error:
            raise CalendarAnimError(f"Invalid frame plan: {path}") from error

    def save_frame_result(self, plan: MultiFramePlan, result: FrameUploadExecutionResult) -> Path:
        path = self.frame_directory(plan, result.frame_index) / "execution-result.json"
        _write_json_atomic(path, result.model_dump_json(indent=2) + "\n")
        return path


def initialize_animation_run(
    plan: MultiFramePlan,
    frame_plans: list[SingleFrameCalendarPlan],
    manifest: AnimationManifest,
    manifest_path: Path,
    store: AnimationRunStore,
) -> AnimationUploadState:
    if len(frame_plans) != len(plan.frames):
        raise CalendarAnimError("Frame plans do not match animation plan")
    store.save_plan(plan)
    state_path = store.state_path(plan.run_id)
    state = store.load_state(plan.run_id) if state_path.exists() else initial_upload_state(plan)
    _validate_state_matches_plan(plan, state)
    if not state_path.exists():
        store.save_state(state)
    performance_path = store.performance_json_path(plan.run_id)
    if not performance_path.exists():
        store.save_performance(initial_performance_report(plan, state))
    for frame_summary, frame_plan in zip(plan.frames, frame_plans, strict=True):
        frame_directory = store.frame_directory(plan, frame_summary.frame_index)
        frame_plan_path = frame_directory / "frame-plan.json"
        if frame_plan_path.exists():
            existing = store.load_frame_plan(plan, frame_summary.frame_index)
            if existing != frame_plan:
                raise CalendarAnimError(
                    f"Frame plan already exists with different content: {frame_plan_path}"
                )
            continue
        source = manifest_path.resolve().parent / manifest.frames[frame_plan.frame_index].image
        write_frame_mapping_artifacts(
            frame_plan,
            source,
            frame_directory,
            source_background=manifest.render.background,
        )
        store.save_frame_result(
            plan,
            FrameUploadExecutionResult(
                executed=False,
                run_id=frame_plan.run_id,
                animation_id=plan.animation_id,
                frame_index=frame_plan.frame_index,
                status=FrameUploadStatus.PENDING,
                planned_events=frame_plan.event_count,
            ),
        )
    store.save_report(plan, state)
    return state


def _validate_state_matches_plan(plan: MultiFramePlan, state: AnimationUploadState) -> None:
    if state.run_id != plan.run_id or state.animation_id != plan.animation_id:
        raise CalendarAnimError("Animation state identity does not match its plan")
    expected = [(frame.frame_index, frame.planned_events) for frame in plan.frames]
    actual = [(frame.frame_index, frame.planned_events) for frame in state.frames]
    if actual != expected:
        raise CalendarAnimError("Animation state frames do not match its plan")


def build_animation_report(plan: MultiFramePlan, state: AnimationUploadState) -> str:
    completed = sum(frame.status is FrameUploadStatus.COMPLETED for frame in state.frames)
    lines = [
        "Multi Frame Calendar Animation",
        "==============================",
        "",
        f"Animation ID: {plan.animation_id}",
        f"Run ID: {plan.run_id}",
        f"Source: {plan.source_file or 'legacy/unspecified'}",
        "Clip: "
        + (
            f"{plan.clip_start_seconds:.3f}-{plan.clip_end_seconds:.3f} seconds "
            f"({plan.clip_duration_seconds:.3f}s at {plan.output_fps:.3f} FPS)"
            if plan.clip_start_seconds is not None
            and plan.clip_end_seconds is not None
            and plan.clip_duration_seconds is not None
            and plan.output_fps is not None
            else "legacy/unspecified"
        ),
        f"Frames: {plan.frame_count}",
        f"Grid: {plan.target_grid_width}x{plan.target_grid_height}",
        f"Grid profile: {plan.grid_profile}",
        f"Slots/day: {plan.slots_per_day or 'legacy/unspecified'}",
        f"Vertical step: {plan.vertical_step_minutes or 'legacy/unspecified'} minutes",
        "Visible window: "
        + (
            f"{plan.visible_start_hour:02d}:00-"
            f"{'00:00' if plan.visible_end_hour == 24 else f'{plan.visible_end_hour:02d}:00'}"
            if plan.visible_start_hour is not None and plan.visible_end_hour is not None
            else "legacy/unspecified"
        ),
        f"Mapping: {plan.mapping_mode.value}",
        f"Event compression: {plan.event_compression.value}",
        f"Palette preset: {plan.palette_preset or 'none'}",
        f"Background colorId: {plan.background_color_id or 'automatic'}",
        "Foreground colorIds: "
        + (", ".join(plan.foreground_color_ids) if plan.foreground_color_ids else "profile"),
        f"Ordering: {plan.subcolumn_order_strategy.value}",
        "Slot keys: "
        + (
            ", ".join(format_summary_key(key) for key in plan.subcolumn_order_keys)
            if plan.subcolumn_order_keys
            else "not used"
        ),
        f"Events/frame: {', '.join(str(value) for value in plan.events_per_frame)}",
        f"Total events: {plan.total_events}",
        f"Per-frame safety limit: {plan.max_events_per_frame}",
        f"Mapper readiness: {'READY' if plan.profile_ready else 'NOT READY'}",
        f"Progress: {completed}/{plan.frame_count} frames completed",
        "",
        "Frames",
        "------",
    ]
    for frame_plan, frame_state in zip(plan.frames, state.frames, strict=True):
        lines.extend(
            [
                f"Frame {frame_plan.frame_index}:",
                "  Source timestamp: "
                + (
                    f"{frame_plan.source_timestamp_seconds:.6f}"
                    if frame_plan.source_timestamp_seconds is not None
                    else "legacy/unspecified"
                ),
                f"  Week: {frame_plan.week_start}",
                f"  Run ID: {frame_plan.frame_run_id}",
                f"  Status: {frame_state.status.value}",
                f"  Events: {frame_state.created_events}/{frame_state.planned_events}",
                f"  Failed events: {frame_state.failed_events}",
                f"  Event retries: {frame_state.event_retry_count}",
                f"  Recovery cycles: {frame_state.recovery_cycles}",
                f"  Duration seconds: {_duration(frame_state.duration_seconds)}",
            ]
        )
    lines.extend(
        [
            "",
            "Upload policy",
            "-------------",
            "Frames are uploaded serially and completed frames are skipped on resume.",
            "Partial frames are reconciled by deterministic event ID and only missing events "
            "are recreated; legacy/inconsistent frames use metadata-scoped cleanup as fallback.",
            "Retryable event failures use bounded exponential backoff with jitter.",
            "The upload stops on the first frame failure.",
            "",
        ]
    )
    return "\n".join(lines)


def _duration(value: float | None) -> str:
    return "pending" if value is None else f"{value:.2f}"
