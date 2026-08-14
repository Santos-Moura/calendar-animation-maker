import json
from pathlib import Path

from calendar_anim.calendar.hybrid_capture.artifacts import HybridCaptureStore
from calendar_anim.calendar.hybrid_capture.models import HybridCapturePlan, HybridFramePlan
from calendar_anim.calendar.multi_frame.artifacts import AnimationRunStore
from calendar_anim.calendar.multi_frame.models import FrameUploadStatus, MultiFramePlan
from calendar_anim.calendar.profiles.store import CalendarProfileStore
from calendar_anim.calendar.recurrence_compaction.hybrid import (
    FINAL_HYBRID_RUN_ID,
    FINAL_INPUT_SHA256,
    FINAL_SOURCE_RUN_ID,
    validate_input_hash,
)
from calendar_anim.calendar.recurrence_upload.artifacts import RecurrenceUploadStore
from calendar_anim.exceptions import CalendarAnimError


def build_final_capture_plan(
    run_id: str = FINAL_HYBRID_RUN_ID,
    *,
    input_file: Path = Path("input.mp4"),
    animation_root: Path = Path("output/animation-runs"),
    hybrid_plan_root: Path = Path("output/hybrid-plans"),
    hybrid_run_root: Path = Path("output/hybrid-runs"),
) -> HybridCapturePlan:
    if run_id != FINAL_HYBRID_RUN_ID:
        raise CalendarAnimError("Only the approved final hybrid run may be captured")
    validate_input_hash(input_file)
    source_store = AnimationRunStore(animation_root)
    source = source_store.load_plan(FINAL_SOURCE_RUN_ID)
    if (
        source.frame_count != 108
        or source.output_fps != 3
        or source.target_grid_width != 126
        or source.target_grid_height != 72
        or source.palette_preset != "cayde-final"
        or source.clip_start_seconds != 114.0
        or source.clip_end_seconds != 150.0
    ):
        raise CalendarAnimError("Final source capture invariants changed")
    hybrid_path = hybrid_plan_root / run_id / "hybrid-final-plan.json"
    try:
        hybrid = json.loads(hybrid_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise CalendarAnimError("Invalid final hybrid plan") from error
    if (
        hybrid.get("input_sha256") != FINAL_INPUT_SHA256
        or hybrid.get("ordering_result") != "PASS"
        or hybrid.get("account_a_frame_indices") != list(range(23))
        or hybrid.get("account_b_frame_indices") != list(range(23, 108))
    ):
        raise CalendarAnimError("Final hybrid boundary or ordering gate changed")
    source_state = source_store.load_state(FINAL_SOURCE_RUN_ID)
    for index in range(23):
        if source_state.frame(index).status is not FrameUploadStatus.COMPLETED:
            raise CalendarAnimError(f"Account-A frame index {index} is not completed")
    recurrence_store = RecurrenceUploadStore(hybrid_plan_root, hybrid_run_root)
    recurrence_state = recurrence_store.load_state(run_id)
    if recurrence_state.completed_count != 32021 or len(recurrence_state.parents) != 32021:
        raise CalendarAnimError("Account-B recurrence bulk is not 32021/32021 completed")
    profiles = CalendarProfileStore()
    account_a = profiles.load("account-a")
    account_b = profiles.load("account-b")
    if account_a.capture_zoom_percent != 33 or account_b.capture_zoom_percent != 90:
        raise CalendarAnimError("Final A/B capture zoom configuration changed")
    frames = []
    for source_frame in source.frames:
        index = source_frame.frame_index
        is_a = index <= 22
        frames.append(
            HybridFramePlan(
                frame_index=index,
                human_frame=index + 1,
                week_start=source_frame.week_start,
                calendar_profile="account-a" if is_a else "account-b",
                calendar_name="Calendar Animation Lab" if is_a else "Calendar Animation Lab B",
                capture_zoom_percent=33 if is_a else 90,
                expected_occurrences=source_frame.planned_events,
                source_frame_plan=str(
                    source_store.frame_directory(source, index) / "frame-plan.json"
                ),
            )
        )
    plan = HybridCapturePlan(
        run_id=run_id,
        source_run_id=FINAL_SOURCE_RUN_ID,
        source_sha256=FINAL_INPUT_SHA256,
        frames=frames,
    )
    HybridCaptureStore(hybrid_run_root).save_plan(plan)
    return plan


def build_account_b_single_profile_capture_plan(
    source: MultiFramePlan,
    run_id: str = FINAL_HYBRID_RUN_ID,
    *,
    source_store: AnimationRunStore | None = None,
) -> HybridCapturePlan:
    """Map all 108 approved weeks to the one visually consistent Account-B profile."""

    if (
        source.frame_count != 108
        or [frame.frame_index for frame in source.frames] != list(range(108))
        or source.output_fps != 3
        or source.target_grid_width != 126
        or source.target_grid_height != 72
        or source.palette_preset != "cayde-final"
        or source.clip_start_seconds != 114.0
        or source.clip_end_seconds != 150.0
    ):
        raise CalendarAnimError("Final single-profile source invariants changed")
    weeks = [frame.week_start for frame in source.frames]
    if any((right - left).days != 7 for left, right in zip(weeks, weeks[1:], strict=False)):
        raise CalendarAnimError("Final single-profile weeks are not consecutive")
    store = source_store or AnimationRunStore()
    frames = [
        HybridFramePlan(
            frame_index=frame.frame_index,
            human_frame=frame.frame_index + 1,
            week_start=frame.week_start,
            calendar_profile="account-b",
            calendar_name="Calendar Animation Lab B",
            capture_zoom_percent=90,
            expected_occurrences=frame.planned_events,
            source_frame_plan=str(
                store.frame_directory(source, frame.frame_index) / "frame-plan.json"
            ),
        )
        for frame in source.frames
    ]
    return HybridCapturePlan(
        schema_version="2.0",
        capture_strategy="single-profile-account-b",
        run_id=run_id,
        source_run_id=FINAL_SOURCE_RUN_ID,
        source_sha256=FINAL_INPUT_SHA256,
        frames=frames,
    )


def parse_human_frames(value: str) -> list[int]:
    try:
        frames = [int(item.strip()) for item in value.split(",") if item.strip()]
    except ValueError as error:
        raise CalendarAnimError("--frames must be comma-separated human frame numbers") from error
    if not frames or len(frames) != len(set(frames)):
        raise CalendarAnimError("--frames must contain unique frame numbers")
    if any(frame < 24 or frame > 108 for frame in frames):
        raise CalendarAnimError("Account-B sanity frames must be in human range 24-108")
    return frames


def parse_single_profile_preview_frames(value: str) -> list[int]:
    try:
        frames = [int(item.strip()) for item in value.split(",") if item.strip()]
    except ValueError as error:
        raise CalendarAnimError("--frames must be comma-separated human frame numbers") from error
    if not frames or len(frames) != len(set(frames)):
        raise CalendarAnimError("--frames must contain unique human frame numbers")
    if any(frame < 1 or frame > 108 for frame in frames):
        raise CalendarAnimError("Preview frames must be in human range 1-108")
    return frames
