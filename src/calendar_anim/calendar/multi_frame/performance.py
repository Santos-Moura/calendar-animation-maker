from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field

from calendar_anim.calendar.multi_frame.models import (
    AnimationUploadState,
    FrameUploadState,
    FrameUploadStatus,
    MultiFramePlan,
)

InvocationStatus = Literal["running", "completed", "stopped", "failed", "interrupted"]


class FrameUploadAttemptPerformance(BaseModel):
    attempt_index: int = Field(ge=1)
    status: FrameUploadStatus
    created_events: int = Field(ge=0)
    failed_events: int = Field(ge=0)
    elapsed_seconds: float = Field(ge=0)


class FrameUploadPerformance(BaseModel):
    frame_index: int = Field(ge=0)
    source_timestamp: float | None = Field(default=None, ge=0)
    week: date
    planned_events: int = Field(ge=0)
    created_events: int = Field(ge=0)
    failed_events: int = Field(ge=0)
    status: FrameUploadStatus
    started_at: datetime | None = None
    finished_at: datetime | None = None
    elapsed_seconds: float | None = Field(default=None, ge=0)
    events_per_second: float | None = Field(default=None, ge=0)
    event_retry_count: int = Field(default=0, ge=0)
    recovery_cycles: int = Field(default=0, ge=0)
    last_failure_retryable: bool | None = None
    initial_attempt_seconds: float | None = Field(default=None, ge=0)
    total_frame_elapsed_seconds: float | None = Field(default=None, ge=0)
    attempts: list[FrameUploadAttemptPerformance] = Field(default_factory=list)


class UploadInvocationPerformance(BaseModel):
    invocation_index: int = Field(ge=1)
    started_at: datetime
    finished_at: datetime | None = None
    elapsed_seconds: float | None = Field(default=None, ge=0)
    status: InvocationStatus = "running"
    frames_previously_completed: list[int] = Field(default_factory=list)
    frames_uploaded_this_invocation: list[int] = Field(default_factory=list)
    frames: list[FrameUploadPerformance] = Field(default_factory=list)


class UploadPerformanceReport(BaseModel):
    schema_version: str = "1.0"
    run_id: str
    animation_id: str
    source_file: str | None = None
    clip_start_seconds: float | None = None
    clip_end_seconds: float | None = None
    clip_duration_seconds: float | None = None
    output_fps: float | None = None
    frame_count: int = Field(gt=0)
    grid_width: int = Field(gt=0)
    grid_height: int = Field(gt=0)
    upload_started_at: datetime | None = None
    upload_finished_at: datetime | None = None
    total_elapsed_seconds: float = Field(default=0, ge=0)
    total_planned_events: int = Field(ge=0)
    total_created_events: int = Field(default=0, ge=0)
    total_failed_events: int = Field(default=0, ge=0)
    overall_events_per_second: float | None = Field(default=None, ge=0)
    average_seconds_per_frame: float | None = Field(default=None, ge=0)
    frames: list[FrameUploadPerformance] = Field(default_factory=list)
    invocations: list[UploadInvocationPerformance] = Field(default_factory=list)


def calculate_events_per_second(events: int, elapsed_seconds: float | None) -> float | None:
    if elapsed_seconds is None or elapsed_seconds <= 0:
        return None
    return events / elapsed_seconds


def initial_performance_report(
    plan: MultiFramePlan, state: AnimationUploadState
) -> UploadPerformanceReport:
    report = UploadPerformanceReport(
        run_id=plan.run_id,
        animation_id=plan.animation_id,
        source_file=plan.source_file,
        clip_start_seconds=plan.clip_start_seconds,
        clip_end_seconds=plan.clip_end_seconds,
        clip_duration_seconds=plan.clip_duration_seconds,
        output_fps=plan.output_fps,
        frame_count=plan.frame_count,
        grid_width=plan.target_grid_width,
        grid_height=plan.target_grid_height,
        total_planned_events=plan.total_events,
    )
    return refresh_performance_report(report, plan, state)


def begin_upload_invocation(
    report: UploadPerformanceReport,
    state: AnimationUploadState,
    started_at: datetime,
) -> UploadInvocationPerformance:
    invocation = UploadInvocationPerformance(
        invocation_index=len(report.invocations) + 1,
        started_at=started_at,
        frames_previously_completed=[
            frame.frame_index
            for frame in state.frames
            if frame.status is FrameUploadStatus.COMPLETED
        ],
    )
    report.invocations.append(invocation)
    if report.upload_started_at is None:
        report.upload_started_at = started_at
    report.upload_finished_at = None
    return invocation


def record_frame_performance(
    invocation: UploadInvocationPerformance,
    plan: MultiFramePlan,
    frame_state: FrameUploadState,
    *,
    attempt_elapsed_seconds: float,
) -> FrameUploadPerformance:
    frame_plan = next(
        frame for frame in plan.frames if frame.frame_index == frame_state.frame_index
    )
    existing = next(
        (frame for frame in invocation.frames if frame.frame_index == frame_state.frame_index),
        None,
    )
    attempts = list(existing.attempts) if existing is not None else []
    attempts.append(
        FrameUploadAttemptPerformance(
            attempt_index=len(attempts) + 1,
            status=frame_state.status,
            created_events=frame_state.created_events,
            failed_events=frame_state.failed_events,
            elapsed_seconds=attempt_elapsed_seconds,
        )
    )
    performance = FrameUploadPerformance(
        frame_index=frame_state.frame_index,
        source_timestamp=frame_plan.source_timestamp_seconds,
        week=frame_plan.week_start,
        planned_events=frame_state.planned_events,
        created_events=frame_state.created_events,
        failed_events=frame_state.failed_events,
        status=frame_state.status,
        started_at=frame_state.frame_started_at,
        finished_at=frame_state.frame_completed_at,
        elapsed_seconds=frame_state.duration_seconds,
        events_per_second=calculate_events_per_second(
            frame_state.created_events, frame_state.duration_seconds
        ),
        event_retry_count=frame_state.event_retry_count,
        recovery_cycles=frame_state.recovery_cycles,
        last_failure_retryable=frame_state.last_failure_retryable,
        initial_attempt_seconds=attempts[0].elapsed_seconds,
        total_frame_elapsed_seconds=frame_state.duration_seconds,
        attempts=attempts,
    )
    invocation.frames = [
        existing
        for existing in invocation.frames
        if existing.frame_index != performance.frame_index
    ]
    invocation.frames.append(performance)
    if performance.frame_index not in invocation.frames_uploaded_this_invocation:
        invocation.frames_uploaded_this_invocation.append(performance.frame_index)
    return performance


def finish_upload_invocation(
    report: UploadPerformanceReport,
    plan: MultiFramePlan,
    state: AnimationUploadState,
    invocation: UploadInvocationPerformance,
    *,
    finished_at: datetime,
    elapsed_seconds: float,
    status: InvocationStatus,
) -> UploadPerformanceReport:
    invocation.finished_at = finished_at
    invocation.elapsed_seconds = max(0.0, elapsed_seconds)
    invocation.status = status
    refreshed = refresh_performance_report(report, plan, state)
    if all(frame.status is FrameUploadStatus.COMPLETED for frame in state.frames):
        refreshed.upload_finished_at = finished_at
    return refreshed


def refresh_performance_report(
    report: UploadPerformanceReport,
    plan: MultiFramePlan,
    state: AnimationUploadState,
) -> UploadPerformanceReport:
    report.frames = [_frame_from_state(plan, frame_state) for frame_state in state.frames]
    attempts = [frame for invocation in report.invocations for frame in invocation.frames]
    measured_invocations = [
        invocation
        for invocation in report.invocations
        if invocation.frames_uploaded_this_invocation and invocation.elapsed_seconds is not None
    ]
    report.total_planned_events = plan.total_events
    report.total_created_events = sum(frame.created_events for frame in attempts)
    report.total_failed_events = sum(frame.failed_events for frame in attempts)
    report.total_elapsed_seconds = sum(
        invocation.elapsed_seconds or 0.0 for invocation in measured_invocations
    )
    report.overall_events_per_second = calculate_events_per_second(
        report.total_created_events, report.total_elapsed_seconds
    )
    report.average_seconds_per_frame = (
        report.total_elapsed_seconds / len(attempts) if attempts else None
    )
    return report


def build_performance_text(report: UploadPerformanceReport) -> str:
    lines = [
        "Cutscene Upload Performance",
        "===========================",
        "",
        f"Run: {report.run_id}",
        f"Grid: {report.grid_width}x{report.grid_height}",
        f"Clip: {_clip(report)}",
        f"FPS: {_number(report.output_fps)}",
        f"Frames: {report.frame_count}",
        "",
        "Frames",
        "------",
    ]
    for frame in report.frames:
        lines.extend(
            [
                f"Frame {frame.frame_index}",
                f"  Source timestamp: {_number(frame.source_timestamp)}",
                f"  Week: {frame.week}",
                f"  Status: {frame.status.value}",
                f"  Events: {frame.planned_events}",
                f"  Created: {frame.created_events}",
                f"  Failed: {frame.failed_events}",
                f"  Started: {_datetime(frame.started_at)}",
                f"  Finished: {_datetime(frame.finished_at)}",
                f"  Elapsed seconds: {_number(frame.elapsed_seconds)}",
                f"  Events/second: {_number(frame.events_per_second)}",
                f"  Event retries: {frame.event_retry_count}",
                f"  Recovery cycles: {frame.recovery_cycles}",
                "  Last failure retryable: " + _optional_bool(frame.last_failure_retryable),
                f"  Initial attempt seconds: {_number(frame.initial_attempt_seconds)}",
                f"  Total frame elapsed seconds: {_number(frame.total_frame_elapsed_seconds)}",
            ]
        )
        for attempt in frame.attempts:
            lines.append(
                f"    Attempt {attempt.attempt_index}: {attempt.status.value}, "
                f"{attempt.created_events}/{frame.planned_events}, "
                f"failed={attempt.failed_events}, elapsed={attempt.elapsed_seconds:.3f}s"
            )
    lines.extend(["", "Invocations", "-----------"])
    for invocation in report.invocations:
        lines.extend(
            [
                f"Invocation {invocation.invocation_index}",
                f"  Status: {invocation.status}",
                f"  Started: {_datetime(invocation.started_at)}",
                f"  Finished: {_datetime(invocation.finished_at)}",
                f"  Elapsed seconds: {_number(invocation.elapsed_seconds)}",
                "  Previously completed: " + _indexes(invocation.frames_previously_completed),
                "  Uploaded this invocation: "
                + _indexes(invocation.frames_uploaded_this_invocation),
            ]
        )
    lines.extend(
        [
            "",
            "Total",
            "-----",
            f"Planned events: {report.total_planned_events}",
            f"Created events: {report.total_created_events}",
            f"Failed events: {report.total_failed_events}",
            f"Elapsed seconds: {_number(report.total_elapsed_seconds)}",
            f"Events/second: {_number(report.overall_events_per_second)}",
            f"Average seconds/frame: {_number(report.average_seconds_per_frame)}",
            "",
        ]
    )
    return "\n".join(lines)


def _frame_from_state(
    plan: MultiFramePlan, frame_state: FrameUploadState
) -> FrameUploadPerformance:
    frame_plan = next(
        frame for frame in plan.frames if frame.frame_index == frame_state.frame_index
    )
    return FrameUploadPerformance(
        frame_index=frame_state.frame_index,
        source_timestamp=frame_plan.source_timestamp_seconds,
        week=frame_plan.week_start,
        planned_events=frame_state.planned_events,
        created_events=frame_state.created_events,
        failed_events=frame_state.failed_events,
        status=frame_state.status,
        started_at=frame_state.frame_started_at,
        finished_at=frame_state.frame_completed_at,
        elapsed_seconds=frame_state.duration_seconds,
        events_per_second=calculate_events_per_second(
            frame_state.created_events, frame_state.duration_seconds
        ),
        event_retry_count=frame_state.event_retry_count,
        recovery_cycles=frame_state.recovery_cycles,
        last_failure_retryable=frame_state.last_failure_retryable,
        total_frame_elapsed_seconds=frame_state.duration_seconds,
    )


def _clip(report: UploadPerformanceReport) -> str:
    if report.clip_start_seconds is None or report.clip_end_seconds is None:
        return "legacy/unspecified"
    return f"{report.clip_start_seconds:.3f}-{report.clip_end_seconds:.3f} seconds"


def _number(value: float | None) -> str:
    return "pending" if value is None else f"{value:.3f}"


def _datetime(value: datetime | None) -> str:
    return "pending" if value is None else value.isoformat()


def _indexes(values: list[int]) -> str:
    return ", ".join(str(value) for value in values) if values else "none"


def _optional_bool(value: bool | None) -> str:
    if value is None:
        return "none"
    return "yes" if value else "no"
