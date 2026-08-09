from datetime import date
from pathlib import Path
from types import TracebackType

import pytest
from PIL import Image
from typer.testing import CliRunner

import calendar_anim.calendar.capture.commands as capture_commands
from calendar_anim.calendar.frame_mapping.models import FrameMappingMode
from calendar_anim.calendar.multi_frame.artifacts import AnimationRunStore, initial_upload_state
from calendar_anim.calendar.multi_frame.models import (
    FrameUploadPlan,
    FrameUploadStatus,
    MultiFramePlan,
)
from calendar_anim.calendar.subcolumn_ordering import SubcolumnOrderStrategy
from calendar_anim.cli import app

pytestmark = pytest.mark.integration
runner = CliRunner()


def _animation_run(tmp_path: Path, *, completed: bool = True) -> Path:
    output_root = tmp_path / "animation-runs"
    frames = [
        FrameUploadPlan(
            frame_index=index,
            week_start=date(2026, 10, 4 + (7 * index)),
            frame_run_id=f"capture-cli-frame-{index:04d}",
            planned_events=1008,
            artifact_directory=f"frames/frame-{index:04d}",
        )
        for index in range(2)
    ]
    plan = MultiFramePlan(
        animation_id="capture-cli",
        run_id="capture-cli",
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
    store = AnimationRunStore(output_root)
    store.save_plan(plan)
    state = initial_upload_state(plan)
    if completed:
        for frame in state.frames:
            frame.status = FrameUploadStatus.COMPLETED
            frame.created_events = frame.planned_events
    store.save_state(state)
    return output_root


class FakePlaywrightGateway:
    instances: list["FakePlaywrightGateway"] = []

    def __init__(self, config: object) -> None:
        self.config = config
        self.weeks: list[date] = []
        self.waits: list[tuple[date, int]] = []
        self.__class__.instances.append(self)

    def __enter__(self) -> "FakePlaywrightGateway":
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        return None

    def open_week(self, week_start: date) -> None:
        self.weeks.append(week_start)

    def wait_until_ready(self, week_start: date, minimum_event_count: int) -> None:
        self.waits.append((week_start, minimum_event_count))

    def capture(self, output_path: Path) -> None:
        Image.new("RGB", (80, 40), "#202124").save(output_path)


def _command(animation_root: Path, capture_root: Path) -> list[str]:
    return [
        "calendar",
        "capture-animation",
        "--run-id",
        "capture-cli",
        "--animation-output-root",
        str(animation_root),
        "--capture-output-root",
        str(capture_root),
        "--stabilization-seconds",
        "0",
    ]


def test_capture_dry_run_writes_plan_without_opening_browser(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    animation_root = _animation_run(tmp_path)
    capture_root = tmp_path / "captures"
    monkeypatch.setattr(
        capture_commands,
        "PlaywrightCalendarCaptureGateway",
        lambda _config: pytest.fail("dry-run must not construct Playwright"),
    )

    result = runner.invoke(app, _command(animation_root, capture_root))

    assert result.exit_code == 0, result.output
    assert "Execution: DRY RUN" in result.output
    assert "Frame 0: CAPTURE" in result.output
    assert "No browser was opened" in result.output
    assert (capture_root / "capture-cli/capture-plan.json").is_file()
    assert (capture_root / "capture-cli/capture-state.json").is_file()
    assert (capture_root / "capture-cli/capture-report.txt").is_file()


def test_capture_execute_uses_fake_browser_and_resume_skips_completed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    FakePlaywrightGateway.instances.clear()
    animation_root = _animation_run(tmp_path)
    capture_root = tmp_path / "captures"
    monkeypatch.setattr(
        capture_commands, "PlaywrightCalendarCaptureGateway", FakePlaywrightGateway
    )
    command = [*_command(animation_root, capture_root), "--execute"]

    first = runner.invoke(app, command)
    second = runner.invoke(app, command)

    assert first.exit_code == 0, first.output
    assert second.exit_code == 0, second.output
    assert "Capture progress: 2/2 completed" in first.output
    assert "Frame 0: completed" in second.output
    assert len(FakePlaywrightGateway.instances) == 2
    assert FakePlaywrightGateway.instances[0].weeks == [date(2026, 10, 4), date(2026, 10, 11)]
    assert FakePlaywrightGateway.instances[1].weeks == []
    assert (capture_root / "capture-cli/frames/frame-0000.png").is_file()
    assert (capture_root / "capture-cli/frames/frame-0001.png").is_file()

    composition = runner.invoke(
        app,
        [
            "calendar",
            "compose-capture",
            "--run-id",
            "capture-cli",
            "--fps",
            "3",
            "--capture-output-root",
            str(capture_root),
        ],
    )
    assert composition.exit_code == 0, composition.output
    assert "Frames: 2" in composition.output
    assert "GIF:" in composition.output
    assert (capture_root / "capture-cli/animation.gif").is_file()


def test_capture_rejects_incomplete_upload_before_browser(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    animation_root = _animation_run(tmp_path, completed=False)
    capture_root = tmp_path / "captures"
    monkeypatch.setattr(
        capture_commands,
        "PlaywrightCalendarCaptureGateway",
        lambda _config: pytest.fail("incomplete upload must not construct Playwright"),
    )

    result = runner.invoke(app, _command(animation_root, capture_root))

    assert result.exit_code == 1
    assert "incomplete frames: 0, 1" in result.output
