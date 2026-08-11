import json
from pathlib import Path

import pytest
from PIL import Image
from typer.testing import CliRunner

import calendar_anim.calendar.multi_frame.commands as multi_commands
from calendar_anim.calendar.calibration.profile import save_profile
from calendar_anim.calendar.fake import FakeCalendarGateway
from calendar_anim.calendar.multi_frame.artifacts import AnimationRunStore
from calendar_anim.calendar.multi_frame.models import FrameUploadStatus
from calendar_anim.cli import app
from calendar_anim.models.frame import AnimationFrame, Block
from calendar_anim.renderer.manifest import write_manifest
from tests.factories import make_manifest, make_ready_calibration_profile

pytestmark = pytest.mark.integration
runner = CliRunner()


def _inputs(tmp_path: Path, frame_count: int = 3) -> tuple[Path, Path]:
    manifest = make_manifest(Block(x=0, y=0, width=1, color_id="1", color_hex="#33B679"))
    manifest.render.frame_count = frame_count
    manifest.frames = [
        AnimationFrame(
            index=index,
            timestamp_seconds=float(index),
            image=f"frames/frame_{index:03d}.png",
            blocks=[Block(x=index % 4, y=0, width=1, color_id="1", color_hex="#33B679")],
        )
        for index in range(frame_count)
    ]
    manifest.statistics.blocks = frame_count
    frames = tmp_path / "frames"
    frames.mkdir()
    for index in range(frame_count):
        Image.new("RGB", (4, 4), "#808080").save(frames / f"frame_{index:03d}.png")
    manifest_path = tmp_path / "animation.json"
    write_manifest(manifest, manifest_path)
    profile_path = tmp_path / "profile.yaml"
    save_profile(make_ready_calibration_profile(), profile_path)
    return manifest_path, profile_path


def _plan_command(
    manifest: Path, profile: Path, output_root: Path, frame_count: int = 3
) -> list[str]:
    return [
        "calendar",
        "plan-animation",
        str(manifest),
        "--profile",
        str(profile),
        "--start-date",
        "2026-10-07",
        "--run-id",
        "cli-animation",
        "--frame-count",
        str(frame_count),
        "--mapping-mode",
        "full-grid",
        "--output-root",
        str(output_root),
    ]


def test_plan_animation_is_local_and_writes_global_and_frame_artifacts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest, profile = _inputs(tmp_path)
    output_root = tmp_path / "runs"
    monkeypatch.setattr(
        multi_commands,
        "_google_gateway",
        lambda: pytest.fail("planning must not construct an API gateway"),
    )

    result = runner.invoke(app, _plan_command(manifest, profile, output_root))

    assert result.exit_code == 0, result.output
    assert "Frames: 3" in result.output
    assert "Weeks: 3 (2026-10-04 onward)" in result.output
    assert "Event compression: synchronized-horizontal-bands" in result.output
    assert "Execution: DRY RUN" in result.output
    run_dir = output_root / "cli-animation"
    assert (run_dir / "animation-plan.json").is_file()
    assert (run_dir / "animation-state.json").is_file()
    assert (run_dir / "animation-report.txt").is_file()
    assert (run_dir / "frames/frame-0002/frame-plan.json").is_file()
    serialized = json.loads((run_dir / "animation-plan.json").read_text(encoding="utf-8"))
    assert serialized["frames"][1]["week_start"] == "2026-10-11"
    assert serialized["event_compression"] == "synchronized-horizontal-bands"
    assert serialized["subcolumn_order_strategy"] == "zero-width"
    assert all(value < 1008 for value in serialized["events_per_frame"])
    assert serialized["total_events"] == sum(serialized["events_per_frame"])


def test_plan_animation_persists_explicit_none_baseline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest, profile = _inputs(tmp_path, frame_count=2)
    output_root = tmp_path / "compressed-runs"
    monkeypatch.setattr(
        multi_commands,
        "_google_gateway",
        lambda: pytest.fail("compressed planning must not construct an API gateway"),
    )
    command = _plan_command(manifest, profile, output_root, 2)
    command.extend(["--event-compression", "none"])

    result = runner.invoke(app, command)

    assert result.exit_code == 0, result.output
    assert "Event compression: none" in result.output
    plan = AnimationRunStore(output_root).load_plan("cli-animation")
    assert plan.event_compression.value == "none"
    assert plan.events_per_frame == [1008, 1008]
    frame_plan = AnimationRunStore(output_root).load_frame_plan(plan, 0)
    assert frame_plan.event_compression.value == "none"
    assert len(frame_plan.mapped_cells) == 1008
    assert len(frame_plan.events) == 1008


def test_plan_animation_persists_explicit_numeric_ordering(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest, profile = _inputs(tmp_path, frame_count=1)
    output_root = tmp_path / "numeric-runs"
    monkeypatch.setattr(
        multi_commands,
        "_google_gateway",
        lambda: pytest.fail("planning must remain local"),
    )
    command = _plan_command(manifest, profile, output_root, 1)
    command.extend(["--subcolumn-ordering", "numeric"])

    result = runner.invoke(app, command)

    assert result.exit_code == 0, result.output
    assert "Subcolumn ordering: numeric" in result.output
    plan = AnimationRunStore(output_root).load_plan("cli-animation")
    assert plan.subcolumn_order_strategy.value == "numeric"
    assert plan.subcolumn_order_keys == ["00", "01", "02", "03", "04", "05"]


def test_plan_animation_accepts_explicit_high_detail_grid_without_changing_defaults(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest, profile = _inputs(tmp_path, frame_count=1)
    output_root = tmp_path / "high-detail-runs"
    monkeypatch.setattr(
        multi_commands,
        "_google_gateway",
        lambda: pytest.fail("high-detail planning must remain local"),
    )
    command = _plan_command(manifest, profile, output_root, 1)
    command.extend(["--experimental-grid", "126x72", "--max-events", "2500"])

    result = runner.invoke(app, command)

    assert result.exit_code == 0, result.output
    assert "Target grid: 126x72" in result.output
    plan = AnimationRunStore(output_root).load_plan("cli-animation")
    assert plan.grid_profile == "high-detail-126x72"
    assert plan.slots_per_day == 18
    assert plan.vertical_step_minutes == 15
    assert (plan.visible_start_hour, plan.visible_end_hour) == (6, 24)
    assert plan.subcolumn_order_strategy.value == "zero-width"
    assert plan.event_compression.value == "synchronized-horizontal-bands"
    assert plan.events_per_frame[0] <= 1200
    assert plan.max_events_per_frame == 2500
    assert plan.source_file == "tiny.avi"
    assert plan.clip_start_seconds == 0
    assert plan.clip_end_seconds == 1
    assert plan.clip_duration_seconds == 1
    assert plan.output_fps == 1
    assert plan.frames[0].source_timestamp_seconds == 0


def test_high_detail_grid_rejects_more_than_2500_events_per_frame(tmp_path: Path) -> None:
    manifest, profile = _inputs(tmp_path, frame_count=1)
    command = _plan_command(manifest, profile, tmp_path / "runs", 1)
    command.extend(["--experimental-grid", "126x72", "--max-events", "2501"])

    result = runner.invoke(app, command)

    assert result.exit_code == 1
    assert "absolute safety limit of 2500" in result.output


def test_final_cutscene_run_has_isolated_5200_event_safety_limit(
    tmp_path: Path,
) -> None:
    manifest, profile = _inputs(tmp_path, frame_count=1)
    command = _plan_command(manifest, profile, tmp_path / "runs", 1)
    run_id_position = command.index("cli-animation")
    command[run_id_position] = "cayde-final-126x72-3fps-36s-01"
    command.extend(["--experimental-grid", "126x72", "--max-events", "5200"])

    result = runner.invoke(app, command)

    assert result.exit_code == 0, result.output
    plan = AnimationRunStore(tmp_path / "runs").load_plan("cayde-final-126x72-3fps-36s-01")
    assert plan.max_events_per_frame == 5200


def test_final_cutscene_run_rejects_more_than_5200_events_per_frame(tmp_path: Path) -> None:
    manifest, profile = _inputs(tmp_path, frame_count=1)
    command = _plan_command(manifest, profile, tmp_path / "runs", 1)
    run_id_position = command.index("cli-animation")
    command[run_id_position] = "cayde-final-126x72-3fps-36s-01"
    command.extend(["--experimental-grid", "126x72", "--max-events", "5201"])

    result = runner.invoke(app, command)

    assert result.exit_code == 1
    assert "absolute safety limit of 5200" in result.output


def test_upload_animation_dry_run_skips_api_and_lists_actions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest, profile = _inputs(tmp_path, frame_count=1)
    output_root = tmp_path / "runs"
    assert runner.invoke(app, _plan_command(manifest, profile, output_root, 1)).exit_code == 0
    monkeypatch.setattr(
        multi_commands,
        "_google_gateway",
        lambda: pytest.fail("upload dry-run must not construct an API gateway"),
    )

    result = runner.invoke(
        app,
        [
            "calendar",
            "upload-animation",
            "--run-id",
            "cli-animation",
            "--output-root",
            str(output_root),
            "--resume",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "Execution: DRY RUN" in result.output
    assert "Frame 0: UPLOAD" in result.output
    assert "No authentication or Calendar API call was made" in result.output


def test_upload_execute_requires_confirmation_before_gateway(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest, profile = _inputs(tmp_path, frame_count=1)
    output_root = tmp_path / "runs"
    assert runner.invoke(app, _plan_command(manifest, profile, output_root, 1)).exit_code == 0
    monkeypatch.setattr(
        multi_commands,
        "_google_gateway",
        lambda: pytest.fail("declined upload must not construct an API gateway"),
    )

    result = runner.invoke(
        app,
        [
            "calendar",
            "upload-animation",
            "--run-id",
            "cli-animation",
            "--output-root",
            str(output_root),
            "--execute",
        ],
        input="n\n",
    )

    assert result.exit_code != 0
    plan = AnimationRunStore(output_root).load_plan("cli-animation")
    assert f"Total planned events: {plan.total_events}" in result.output
    assert plan.total_events < 1008
    assert "Continue?" in result.output


def test_upload_execute_uses_fake_gateway_and_persists_completion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest, profile = _inputs(tmp_path, frame_count=1)
    output_root = tmp_path / "runs"
    assert runner.invoke(app, _plan_command(manifest, profile, output_root, 1)).exit_code == 0
    gateway = FakeCalendarGateway()
    monkeypatch.setattr(multi_commands, "_google_gateway", lambda: gateway)
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(
        app,
        [
            "calendar",
            "upload-animation",
            "--run-id",
            "cli-animation",
            "--output-root",
            str(output_root),
            "--execute",
            "--yes",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "Animation progress: 1/1 completed" in result.output
    state = AnimationRunStore(output_root).load_state("cli-animation")
    assert state.frames[0].status is FrameUploadStatus.COMPLETED
    assert state.frames[0].created_events == state.frames[0].planned_events < 1008


def test_cleanup_dry_run_is_local_and_invalid_run_is_clear(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest, profile = _inputs(tmp_path, frame_count=1)
    output_root = tmp_path / "runs"
    assert runner.invoke(app, _plan_command(manifest, profile, output_root, 1)).exit_code == 0
    monkeypatch.setattr(
        multi_commands,
        "_google_gateway",
        lambda: pytest.fail("cleanup dry-run must not construct an API gateway"),
    )

    cleanup = runner.invoke(
        app,
        [
            "calendar",
            "cleanup-animation",
            "--run-id",
            "cli-animation",
            "--frame",
            "0",
            "--output-root",
            str(output_root),
        ],
    )
    invalid = runner.invoke(
        app,
        [
            "calendar",
            "upload-animation",
            "--run-id",
            "../bad",
            "--output-root",
            str(output_root),
        ],
    )

    assert cleanup.exit_code == 0, cleanup.output
    assert "No authentication, Calendar lookup, or deletion was performed" in cleanup.output
    assert invalid.exit_code == 1
    assert "Invalid run-id" in invalid.output
