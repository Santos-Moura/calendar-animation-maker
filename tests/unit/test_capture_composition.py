from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from PIL import Image

from calendar_anim.calendar.capture.artifacts import CaptureStore, initial_capture_state
from calendar_anim.calendar.capture.composition import (
    compose_gif,
    validate_completed_capture,
)
from calendar_anim.calendar.capture.models import (
    CalendarCaptureConfig,
    CaptureFramePlan,
    CapturePlan,
    FrameCaptureStatus,
)
from calendar_anim.exceptions import CalendarAnimError

pytestmark = pytest.mark.unit


def _completed_capture(tmp_path: Path) -> tuple[CapturePlan, CaptureStore]:
    plan = CapturePlan(
        run_id="composition-test",
        animation_id="composition-test",
        source_plan_digest="a" * 64,
        frame_count=3,
        config=CalendarCaptureConfig(),
        frames=[
            CaptureFramePlan(
                frame_index=index,
                week_start=date(2026, 10, 4 + (7 * index)),
                planned_events=1008,
                screenshot_path=f"frames/frame-{index:04d}.png",
            )
            for index in range(3)
        ],
    )
    store = CaptureStore(tmp_path / "captures")
    state = initial_capture_state(plan)
    colors = ["#ff0000", "#ff0000", "#0000ff"]
    for frame, color in zip(state.frames, colors, strict=True):
        frame.status = FrameCaptureStatus.COMPLETED
        frame.started_at = datetime.now(UTC)
        frame.completed_at = datetime.now(UTC)
        path = store.screenshot_path(plan, frame.frame_index)
        path.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (40, 20), color).save(path)
    store.save_plan(plan)
    store.save_state(state)
    return plan, store


def test_gif_composition_preserves_frame_order_and_duplicate_duration(tmp_path: Path) -> None:
    plan, store = _completed_capture(tmp_path)
    state = store.load_state(plan.run_id)
    frames = validate_completed_capture(plan, state, store)

    output = compose_gif(frames, store.run_directory(plan.run_id) / "animation.gif", fps=2)

    with Image.open(output) as image:
        assert image.n_frames == 3
        durations: list[int] = []
        colors: list[tuple[int, int, int]] = []
        for index in range(image.n_frames):
            image.seek(index)
            durations.append(int(image.info["duration"]))
            colors.append(image.convert("RGB").getpixel((10, 10)))
    assert sum(durations) == 1500
    assert durations == [500, 500, 500]
    assert colors[0] == (255, 0, 0)
    assert colors[-1] == (0, 0, 255)


def test_composition_rejects_incomplete_or_inconsistent_frames(tmp_path: Path) -> None:
    plan, store = _completed_capture(tmp_path)
    state = store.load_state(plan.run_id)
    state.frames[1].status = FrameCaptureStatus.FAILED
    with pytest.raises(CalendarAnimError, match="completed captures: 1"):
        validate_completed_capture(plan, state, store)

    paths = [store.screenshot_path(plan, frame.frame_index) for frame in plan.frames]
    Image.new("RGB", (41, 20), "#000000").save(paths[2])
    with pytest.raises(CalendarAnimError, match="consistent dimensions"):
        compose_gif(paths, tmp_path / "bad.gif", fps=3)
