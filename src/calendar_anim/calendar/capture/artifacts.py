import hashlib
import os
import shutil
from datetime import UTC, datetime
from pathlib import Path

from pydantic import ValidationError

from calendar_anim.calendar.capture.models import (
    CalendarCaptureConfig,
    CaptureFramePlan,
    CapturePlan,
    CaptureState,
    FrameCaptureState,
    FrameCaptureStatus,
)
from calendar_anim.calendar.multi_frame.artifacts import AnimationRunStore
from calendar_anim.calendar.multi_frame.models import FrameUploadStatus
from calendar_anim.exceptions import CalendarAnimError


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


def build_capture_plan(
    run_id: str,
    animation_store: AnimationRunStore,
    config: CalendarCaptureConfig,
) -> CapturePlan:
    animation_plan = animation_store.load_plan(run_id)
    upload_state = animation_store.load_state(run_id)
    if (
        upload_state.run_id != animation_plan.run_id
        or upload_state.animation_id != animation_plan.animation_id
    ):
        raise CalendarAnimError("Animation upload state identity does not match its plan")
    expected = [(frame.frame_index, frame.planned_events) for frame in animation_plan.frames]
    actual = [(frame.frame_index, frame.planned_events) for frame in upload_state.frames]
    if actual != expected:
        raise CalendarAnimError("Animation upload state frames do not match its plan")
    incomplete = [
        frame.frame_index
        for frame in upload_state.frames
        if frame.status is not FrameUploadStatus.COMPLETED
    ]
    if incomplete:
        indexes = ", ".join(str(index) for index in incomplete)
        raise CalendarAnimError(f"Capture requires completed uploads; incomplete frames: {indexes}")
    plan_bytes = animation_store.plan_path(run_id).read_bytes()
    digest = hashlib.sha256(plan_bytes).hexdigest()
    return CapturePlan(
        run_id=animation_plan.run_id,
        animation_id=animation_plan.animation_id,
        source_plan_digest=digest,
        frame_count=animation_plan.frame_count,
        config=config,
        frames=[
            CaptureFramePlan(
                frame_index=frame.frame_index,
                week_start=frame.week_start,
                planned_events=frame.planned_events,
                screenshot_path=f"frames/frame-{frame.frame_index:04d}.png",
            )
            for frame in animation_plan.frames
        ],
    )


def initial_capture_state(plan: CapturePlan) -> CaptureState:
    return CaptureState(
        run_id=plan.run_id,
        source_plan_digest=plan.source_plan_digest,
        frames=[
            FrameCaptureState(
                frame_index=frame.frame_index,
                screenshot_path=frame.screenshot_path,
            )
            for frame in plan.frames
        ],
        updated_at=datetime.now(UTC),
    )


class CaptureStore:
    def __init__(self, root: Path = Path("output/captures")) -> None:
        self.root = root

    def run_directory(self, run_id: str) -> Path:
        return self.root / run_id

    def plan_path(self, run_id: str) -> Path:
        return self.run_directory(run_id) / "capture-plan.json"

    def state_path(self, run_id: str) -> Path:
        return self.run_directory(run_id) / "capture-state.json"

    def report_path(self, run_id: str) -> Path:
        return self.run_directory(run_id) / "capture-report.txt"

    def save_plan(self, plan: CapturePlan) -> Path:
        path = self.plan_path(plan.run_id)
        if path.exists():
            if self.load_plan(plan.run_id) != plan:
                raise CalendarAnimError(f"Capture plan already has different content: {path}")
            return path
        _write_json_atomic(path, plan.model_dump_json(indent=2) + "\n")
        return path

    def load_plan(self, run_id: str) -> CapturePlan:
        path = self.plan_path(run_id)
        try:
            return CapturePlan.model_validate_json(path.read_text(encoding="utf-8"))
        except FileNotFoundError as error:
            raise CalendarAnimError(f"Capture plan does not exist: {run_id}") from error
        except (OSError, ValidationError, ValueError) as error:
            raise CalendarAnimError(f"Invalid capture plan: {path}") from error

    def save_state(self, state: CaptureState) -> Path:
        state.updated_at = datetime.now(UTC)
        path = self.state_path(state.run_id)
        _write_json_atomic(path, state.model_dump_json(indent=2) + "\n")
        return path

    def load_state(self, run_id: str) -> CaptureState:
        path = self.state_path(run_id)
        try:
            return CaptureState.model_validate_json(path.read_text(encoding="utf-8"))
        except FileNotFoundError as error:
            raise CalendarAnimError(f"Capture state does not exist: {run_id}") from error
        except (OSError, ValidationError, ValueError) as error:
            raise CalendarAnimError(f"Invalid capture state: {path}") from error

    def initialize(self, plan: CapturePlan) -> CaptureState:
        self.save_plan(plan)
        path = self.state_path(plan.run_id)
        state = self.load_state(plan.run_id) if path.exists() else initial_capture_state(plan)
        self._validate_state(plan, state)
        if not path.exists():
            self.save_state(state)
        self.save_report(plan, state)
        return state

    def screenshot_path(self, plan: CapturePlan, frame_index: int) -> Path:
        frame = next((item for item in plan.frames if item.frame_index == frame_index), None)
        if frame is None:
            raise CalendarAnimError(f"Capture plan has no frame {frame_index}")
        return self.run_directory(plan.run_id) / frame.screenshot_path

    def save_report(self, plan: CapturePlan, state: CaptureState) -> Path:
        path = self.report_path(plan.run_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(build_capture_report(plan, state), encoding="utf-8")
        return path

    def reset_for_recapture(self, plan: CapturePlan, state: CaptureState) -> Path | None:
        self._validate_state(plan, state)
        timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        backup_directory = self.run_directory(plan.run_id) / "backups" / timestamp
        copied = False
        for plan_frame in plan.frames:
            source = self.screenshot_path(plan, plan_frame.frame_index)
            if source.is_file():
                destination = backup_directory / plan_frame.screenshot_path
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, destination)
                copied = True
        for name in ("animation.gif", "animation.mp4"):
            source = self.run_directory(plan.run_id) / name
            if source.is_file():
                backup_directory.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, backup_directory / name)
                copied = True
        for state_frame in state.frames:
            state_frame.status = FrameCaptureStatus.PENDING
            state_frame.started_at = None
            state_frame.completed_at = None
            state_frame.error = None
        self.save_state(state)
        self.save_report(plan, state)
        return backup_directory if copied else None

    @staticmethod
    def _validate_state(plan: CapturePlan, state: CaptureState) -> None:
        if state.run_id != plan.run_id or state.source_plan_digest != plan.source_plan_digest:
            raise CalendarAnimError("Capture state identity does not match its plan")
        expected = [(frame.frame_index, frame.screenshot_path) for frame in plan.frames]
        actual = [(frame.frame_index, frame.screenshot_path) for frame in state.frames]
        if actual != expected:
            raise CalendarAnimError("Capture state frames do not match its plan")


def build_capture_report(plan: CapturePlan, state: CaptureState) -> str:
    completed = sum(frame.status is FrameCaptureStatus.COMPLETED for frame in state.frames)
    lines = [
        "Google Calendar Week Capture",
        "============================",
        "",
        f"Animation ID: {plan.animation_id}",
        f"Run ID: {plan.run_id}",
        f"Frames: {plan.frame_count}",
        f"Progress: {completed}/{plan.frame_count} completed",
        f"Viewport: {plan.config.viewport_width}x{plan.config.viewport_height}",
        f"Device scale factor: {plan.config.device_scale_factor}",
        f"Browser zoom: {plan.config.browser_zoom_percent}%",
        f"Theme: {plan.config.color_scheme}",
        f"Browser channel: {plan.config.browser_channel.value}",
        f"View: {plan.config.calendar_view}",
        f"Sidebar hidden: {plan.config.sidebar_hidden}",
        f"Visible hours: {plan.config.visible_start_hour:02d}:00-"
        f"{plan.config.visible_end_hour:02d}:00",
        "",
        "Frames",
        "------",
    ]
    for frame_plan, frame_state in zip(plan.frames, state.frames, strict=True):
        lines.extend(
            [
                f"Frame {frame_plan.frame_index}:",
                f"  Week: {frame_plan.week_start}",
                f"  Status: {frame_state.status.value}",
                f"  Screenshot: {frame_state.screenshot_path}",
                f"  Error: {frame_state.error or 'none'}",
            ]
        )
    lines.extend(
        [
            "",
            "Policy",
            "------",
            "Weeks come from the immutable animation plan; capture never recalculates them.",
            "Completed screenshots are preserved and skipped on resume.",
            "Calendar event creation and deletion are outside the browser capture boundary.",
            "",
        ]
    )
    return "\n".join(lines)
