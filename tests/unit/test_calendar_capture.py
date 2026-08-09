from datetime import UTC, date, datetime
from pathlib import Path
from unittest.mock import patch

import pytest
from PIL import Image

from calendar_anim.calendar.capture.artifacts import (
    CaptureStore,
    build_capture_plan,
    initial_capture_state,
)
from calendar_anim.calendar.capture.models import CalendarCaptureConfig, FrameCaptureStatus
from calendar_anim.calendar.capture.service import CalendarWeekCaptureService
from calendar_anim.calendar.frame_mapping.models import FrameMappingMode
from calendar_anim.calendar.multi_frame.artifacts import AnimationRunStore, initial_upload_state
from calendar_anim.calendar.multi_frame.models import (
    FrameUploadPlan,
    FrameUploadStatus,
    MultiFramePlan,
)
from calendar_anim.calendar.subcolumn_ordering import SubcolumnOrderStrategy
from calendar_anim.exceptions import CalendarAnimError

pytestmark = pytest.mark.unit


class FakeBrowser:
    def __init__(self, fail_on_week: date | None = None) -> None:
        self.fail_on_week = fail_on_week
        self.opened: list[date] = []
        self.waited: list[tuple[date, int]] = []
        self.captured: list[Path] = []

    def open_week(self, week_start: date) -> None:
        self.opened.append(week_start)

    def wait_until_ready(self, week_start: date, minimum_event_count: int) -> None:
        self.waited.append((week_start, minimum_event_count))
        if week_start == self.fail_on_week:
            raise RuntimeError("calendar did not stabilize")

    def capture(self, output_path: Path) -> None:
        self.captured.append(output_path)
        Image.new("RGB", (40, 20), "#112233").save(output_path)


def _uploaded_animation(tmp_path: Path) -> AnimationRunStore:
    frames = [
        FrameUploadPlan(
            frame_index=index,
            week_start=date(2026, 10, 4 + (7 * index)),
            frame_run_id=f"capture-test-frame-{index:04d}",
            planned_events=1008,
            artifact_directory=f"frames/frame-{index:04d}",
        )
        for index in range(2)
    ]
    plan = MultiFramePlan(
        animation_id="capture-test",
        run_id="capture-test",
        timezone="America/Sao_Paulo",
        start_week=date(2026, 10, 4),
        frame_start=0,
        frame_count=2,
        mapping_mode=FrameMappingMode.FULL_GRID,
        target_grid_width=42,
        target_grid_height=24,
        subcolumn_order_strategy=SubcolumnOrderStrategy.SUMMARY_PREFIX,
        subcolumn_order_keys=[f"{index:02d}" for index in range(6)],
        max_events_per_frame=1200,
        profile_ready=True,
        events_per_frame=[1008, 1008],
        total_events=2016,
        frames=frames,
    )
    store = AnimationRunStore(tmp_path / "animation-runs")
    store.save_plan(plan)
    state = initial_upload_state(plan)
    for frame in state.frames:
        frame.status = FrameUploadStatus.COMPLETED
        frame.created_events = frame.planned_events
    store.save_state(state)
    return store


def test_capture_plan_uses_persisted_weeks_and_requires_completed_uploads(
    tmp_path: Path,
) -> None:
    animation_store = _uploaded_animation(tmp_path)

    plan = build_capture_plan(
        "capture-test", animation_store, CalendarCaptureConfig(stabilization_seconds=0)
    )

    assert [frame.week_start for frame in plan.frames] == [
        date(2026, 10, 4),
        date(2026, 10, 11),
    ]
    state = animation_store.load_state("capture-test")
    state.frames[1].status = FrameUploadStatus.FAILED
    animation_store.save_state(state)
    with pytest.raises(CalendarAnimError, match="incomplete frames: 1"):
        build_capture_plan("capture-test", animation_store, CalendarCaptureConfig())

    state.frames[1].status = FrameUploadStatus.COMPLETED
    state.frames[1].created_events = state.frames[1].planned_events
    state.frames.pop(0)
    animation_store.save_state(state)
    with pytest.raises(CalendarAnimError, match="state frames do not match"):
        build_capture_plan("capture-test", animation_store, CalendarCaptureConfig())


def test_capture_store_checkpoints_atomically_and_rejects_plan_drift(tmp_path: Path) -> None:
    animation_store = _uploaded_animation(tmp_path)
    plan = build_capture_plan("capture-test", animation_store, CalendarCaptureConfig())
    store = CaptureStore(tmp_path / "captures")
    state = initial_capture_state(plan)

    with patch(
        "calendar_anim.calendar.capture.artifacts.os.replace",
        wraps=__import__("os").replace,
    ) as replace:
        store.save_state(state)

    assert replace.call_count == 1
    assert store.load_state(plan.run_id) == state
    store.save_plan(plan)
    changed = plan.model_copy(
        update={"config": plan.config.model_copy(update={"browser_zoom_percent": 90})}
    )
    with pytest.raises(CalendarAnimError, match="different content"):
        store.save_plan(changed)


def test_capture_service_skips_completed_frames_and_preserves_order(tmp_path: Path) -> None:
    animation_store = _uploaded_animation(tmp_path)
    plan = build_capture_plan("capture-test", animation_store, CalendarCaptureConfig())
    store = CaptureStore(tmp_path / "captures")
    state = store.initialize(plan)
    first = state.frames[0]
    first.status = FrameCaptureStatus.COMPLETED
    first.started_at = datetime.now(UTC)
    first.completed_at = datetime.now(UTC)
    first_path = store.screenshot_path(plan, 0)
    first_path.parent.mkdir(parents=True)
    Image.new("RGB", (40, 20), "#334455").save(first_path)
    store.save_state(state)
    browser = FakeBrowser()

    completed = CalendarWeekCaptureService(browser, store).capture(plan, state)

    assert browser.opened == [date(2026, 10, 11)]
    assert browser.waited == [(date(2026, 10, 11), 1008)]
    assert browser.captured == [store.screenshot_path(plan, 1)]
    assert all(frame.status is FrameCaptureStatus.COMPLETED for frame in completed.frames)
    assert "Progress: 2/2 completed" in store.report_path(plan.run_id).read_text()


def test_capture_failure_is_checkpointed_and_stops_the_run(tmp_path: Path) -> None:
    animation_store = _uploaded_animation(tmp_path)
    plan = build_capture_plan("capture-test", animation_store, CalendarCaptureConfig())
    store = CaptureStore(tmp_path / "captures")
    state = store.initialize(plan)
    browser = FakeBrowser(fail_on_week=date(2026, 10, 4))

    with pytest.raises(RuntimeError, match="did not stabilize"):
        CalendarWeekCaptureService(browser, store).capture(plan, state)

    persisted = store.load_state(plan.run_id)
    assert persisted.frames[0].status is FrameCaptureStatus.FAILED
    assert persisted.frames[0].error == "calendar did not stabilize"
    assert persisted.frames[1].status is FrameCaptureStatus.PENDING


def test_recapture_backs_up_outputs_and_resets_every_frame(tmp_path: Path) -> None:
    animation_store = _uploaded_animation(tmp_path)
    plan = build_capture_plan("capture-test", animation_store, CalendarCaptureConfig())
    store = CaptureStore(tmp_path / "captures")
    state = store.initialize(plan)
    for frame in state.frames:
        frame.status = FrameCaptureStatus.COMPLETED
        frame.started_at = datetime.now(UTC)
        frame.completed_at = datetime.now(UTC)
        screenshot = store.screenshot_path(plan, frame.frame_index)
        screenshot.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (40, 20), "#334455").save(screenshot)
    store.save_state(state)
    gif = store.run_directory(plan.run_id) / "animation.gif"
    gif.write_bytes(b"old-gif")

    backup = store.reset_for_recapture(plan, state)

    assert backup is not None
    assert (backup / "frames/frame-0000.png").is_file()
    assert (backup / "frames/frame-0001.png").is_file()
    assert (backup / "animation.gif").read_bytes() == b"old-gif"
    assert store.screenshot_path(plan, 0).is_file()
    persisted = store.load_state(plan.run_id)
    assert all(frame.status is FrameCaptureStatus.PENDING for frame in persisted.frames)
    assert all(frame.started_at is None for frame in persisted.frames)
