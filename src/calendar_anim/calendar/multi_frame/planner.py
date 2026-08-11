from datetime import date, timedelta
from hashlib import sha256

from calendar_anim.calendar.calibration.models import CalibrationProfile
from calendar_anim.calendar.frame_mapping.mapper import (
    build_single_frame_plan,
    resolve_week_start,
)
from calendar_anim.calendar.frame_mapping.models import (
    DEFAULT_EVENT_COMPRESSION,
    EventCompressionMode,
    FitMode,
    FrameMappingMode,
    SingleFrameCalendarPlan,
)
from calendar_anim.calendar.multi_frame.models import FrameUploadPlan, MultiFramePlan
from calendar_anim.calendar.subcolumn_ordering import SubcolumnOrderStrategy
from calendar_anim.exceptions import CalendarAnimError
from calendar_anim.models.animation import AnimationManifest


def frame_run_id(run_id: str, frame_index: int) -> str:
    suffix = f"-frame-{frame_index:04d}"
    if len(run_id) + len(suffix) <= 64:
        return f"{run_id}{suffix}"
    digest = sha256(run_id.encode("utf-8")).hexdigest()[:8]
    collision_safe_suffix = f"-{digest}{suffix}"
    return f"{run_id[: 64 - len(collision_safe_suffix)]}{collision_safe_suffix}"


def build_multi_frame_plan(
    manifest: AnimationManifest,
    profile: CalibrationProfile,
    *,
    frame_start: int,
    frame_count: int,
    anchor_date: date,
    run_id: str,
    max_events_per_frame: int,
    fit: FitMode = "contain",
    calendar_name: str = "Calendar Animation Lab",
    mapping_mode: FrameMappingMode = FrameMappingMode.FULL_GRID,
    event_compression: EventCompressionMode = DEFAULT_EVENT_COMPRESSION,
    calendar_background_color_id: str | None = None,
    subcolumn_order_strategy: str | SubcolumnOrderStrategy | None = None,
    grid_profile: str = "production",
) -> tuple[MultiFramePlan, list[SingleFrameCalendarPlan]]:
    if frame_start < 0:
        raise CalendarAnimError("frame start must be non-negative")
    if frame_count <= 0:
        raise CalendarAnimError("frame count must be positive")
    frame_end = frame_start + frame_count
    if frame_end > len(manifest.frames):
        raise CalendarAnimError(
            f"Frame range {frame_start}:{frame_end} exceeds manifest with "
            f"{len(manifest.frames)} frames"
        )
    week_starts_on = profile.position_mapping.week_starts_on
    if week_starts_on is None:
        raise CalendarAnimError(
            "Calibration profile has no week_starts_on observation; record position-grid first"
        )
    start_week = resolve_week_start(anchor_date, week_starts_on)
    frame_plans: list[SingleFrameCalendarPlan] = []
    upload_frames: list[FrameUploadPlan] = []
    for offset, selected_index in enumerate(range(frame_start, frame_end)):
        week_start = start_week + timedelta(weeks=offset)
        single = build_single_frame_plan(
            manifest,
            profile,
            frame_index=selected_index,
            anchor_date=week_start,
            run_id=frame_run_id(run_id, selected_index),
            max_execute_events=max_events_per_frame,
            fit=fit,
            calendar_name=calendar_name,
            mapping_mode=mapping_mode,
            event_compression=event_compression,
            calendar_background_color_id=calendar_background_color_id,
            subcolumn_order_strategy=subcolumn_order_strategy,
        )
        if single.event_count > max_events_per_frame:
            raise CalendarAnimError(
                f"Frame {selected_index} requires {single.event_count} events, above the "
                f"per-frame limit of {max_events_per_frame}"
            )
        frame_plans.append(single)
        upload_frames.append(
            FrameUploadPlan(
                frame_index=selected_index,
                source_timestamp_seconds=manifest.frames[selected_index].timestamp_seconds,
                week_start=single.week_start_date,
                frame_run_id=single.run_id,
                planned_events=single.event_count,
                artifact_directory=f"frames/frame-{selected_index:04d}",
            )
        )
    first = frame_plans[0]
    event_counts = [plan.event_count for plan in frame_plans]
    return (
        MultiFramePlan(
            animation_id=manifest.animation_id,
            run_id=run_id,
            calendar_name=calendar_name,
            timezone=profile.calendar_ui.timezone,
            source_file=manifest.source.file_name,
            clip_start_seconds=manifest.source.start_seconds,
            clip_end_seconds=manifest.source.start_seconds + manifest.source.duration_seconds,
            clip_duration_seconds=manifest.source.duration_seconds,
            output_fps=manifest.render.output_fps,
            start_week=start_week,
            frame_start=frame_start,
            frame_count=frame_count,
            mapping_mode=mapping_mode,
            event_compression=event_compression,
            target_grid_width=first.target_grid_width,
            target_grid_height=first.target_grid_height,
            grid_profile=grid_profile,
            slots_per_day=profile.horizontal_mapping.usable_overlap_columns_per_day,
            vertical_step_minutes=(profile.vertical_mapping.minimum_distinguishable_height_minutes),
            visible_start_hour=profile.calendar_ui.visible_start_hour,
            visible_end_hour=profile.calendar_ui.visible_end_hour,
            subcolumn_order_strategy=first.subcolumn_order_strategy,
            subcolumn_order_keys=first.subcolumn_order_keys,
            max_events_per_frame=max_events_per_frame,
            profile_ready=all(plan.profile_ready for plan in frame_plans),
            events_per_frame=event_counts,
            total_events=sum(event_counts),
            frames=upload_frames,
        ),
        frame_plans,
    )
