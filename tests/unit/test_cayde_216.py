from datetime import date, timedelta
from pathlib import Path

import pytest
from PIL import Image

from calendar_anim.calendar.capture.final_media import FFmpegTools
from calendar_anim.calendar.cayde_216.models import (
    Cayde216SizingReport,
    FrameOccurrenceStatistics,
    PayloadSizing,
)
from calendar_anim.calendar.cayde_216.planner import (
    FIRST_WEEK,
    FPS,
    FRAME_COUNT,
    OLD_LAST_WEEK,
    RUN_ID,
)
from calendar_anim.calendar.hybrid_capture.media import (
    FinalVisualProbe,
    build_final_visual_command,
    inspect_final_frames,
    validate_final_visual_probe,
)
from calendar_anim.calendar.recurrence_compaction.planner import _parent_id
from calendar_anim.exceptions import CalendarAnimError


def _report(**updates: object) -> Cayde216SizingReport:
    weeks = [FIRST_WEEK + timedelta(weeks=index) for index in range(216)]
    values: dict[str, object] = {
        "run_id": RUN_ID,
        "source_file": "input.mp4",
        "source_sha256": "a" * 64,
        "clip_start_seconds": 114.0,
        "clip_end_seconds": 150.0,
        "duration_seconds": 36.0,
        "fps": 6.0,
        "frame_count": 216,
        "frame_indices": list(range(216)),
        "calendar_profile": "account-b",
        "calendar_name": "Calendar Animation Lab B",
        "timezone": "America/Sao_Paulo",
        "first_week": weeks[0],
        "last_week": weeks[-1],
        "week_count": 216,
        "all_week_deltas_seven_days": True,
        "old_first_week": date(2027, 10, 10),
        "old_last_week": OLD_LAST_WEEK,
        "old_week_overlap": 0,
        "logical_occurrences": 555_000,
        "frame_occurrences": FrameOccurrenceStatistics(minimum=1, mean=2.0, p95=3, maximum=4),
        "unique_recurrence_signatures": 100,
        "recurring_parents": 80_000,
        "reduction_percent": 85.0,
        "singleton_parents": 10,
        "largest_group": 216,
        "largest_chunk": 100,
        "largest_rdate_count": 99,
        "expansion_missing": 0,
        "expansion_extra": 0,
        "expansion_duplicates": 0,
        "expansion_exact": True,
        "parent_ids_unique": True,
        "parent_id_collisions_with_existing_b": 0,
        "existing_b_parent_count": 46_468,
        "payload": PayloadSizing(
            minimum_bytes=500,
            mean_bytes=800,
            p95_bytes=1000,
            maximum_bytes=1200,
            within_safe_limit=True,
        ),
        "eta_seconds": {"0.75": 60_000, "1.0": 80_000, "1.5": 120_000, "2.0": 160_000},
        "logical_occurrence_ratio": 2.0,
        "parent_count_ratio": 1.72,
        "upload_eta_ratio": 1.72,
        "readiness_protection": "empty capture retries without checkpoint",
        "future_preview_human_frames": [1, 54, 108, 162, 216],
        "old_protected_sha256_before": {"old": "hash"},
        "old_protected_sha256_after": {"old": "hash"},
    }
    values.update(updates)
    return Cayde216SizingReport.model_validate(values)


def test_cayde_216_timing_and_week_mapping_are_exact() -> None:
    weeks = [FIRST_WEEK + timedelta(weeks=index) for index in range(FRAME_COUNT)]

    assert date(2029, 10, 28) == OLD_LAST_WEEK
    assert date(2029, 11, 4) == FIRST_WEEK
    assert FRAME_COUNT / FPS == 36.0
    assert weeks[-1] == date(2033, 12, 18)
    assert all(
        right - left == timedelta(days=7) for left, right in zip(weeks, weeks[1:], strict=False)
    )
    assert not set(weeks) & {date(2027, 10, 10) + timedelta(weeks=index) for index in range(108)}


def test_cayde_216_report_rejects_overlap_collision_or_expansion_difference() -> None:
    _report()
    with pytest.raises(ValueError, match="old week overlap"):
        _report(old_week_overlap=1)
    with pytest.raises(ValueError, match="parent IDs"):
        _report(parent_id_collisions_with_existing_b=1)
    with pytest.raises(ValueError, match="recurrence expansion"):
        _report(expansion_exact=False, expansion_missing=1)


def test_cayde_216_parent_ids_are_deterministic_and_namespaced() -> None:
    keys = ["f0000:event-a", "f0001:event-a"]
    first = _parent_id(RUN_ID, "signature", "group", 0, keys)

    assert first == _parent_id(RUN_ID, "signature", "group", 0, keys)
    assert first != _parent_id("old-run", "signature", "group", 0, keys)


def test_cayde_216_composer_contract_is_000_through_215_at_6fps(tmp_path: Path) -> None:
    frames = tmp_path / "frames"
    frames.mkdir()
    for index in range(216):
        Image.new("RGB", (14, 8), (index % 256, 0, 0)).save(frames / f"frame_{index:03d}.png")
    sequence = inspect_final_frames(frames, (14, 8), frame_count=216)
    command = build_final_visual_command(
        FFmpegTools(Path("ffmpeg"), Path("ffprobe"), "test"),
        frames,
        tmp_path / "final.mp4",
        frame_count=216,
        fps=6,
    )

    assert sequence.count == 216
    assert sequence.first.name == "frame_000.png"
    assert sequence.last.name == "frame_215.png"
    assert command[command.index("-framerate") + 1] == "6"
    assert command[command.index("-frames:v") + 1] == "216"
    validate_final_visual_probe(
        FinalVisualProbe("h264", "High", 1512, 864, 6.0, 216, 36.0, "1:1"),
        (1512, 864),
        expected_frame_count=216,
        expected_fps=6,
        expected_duration_seconds=36,
    )


def test_cayde_216_composer_rejects_missing_last_frame(tmp_path: Path) -> None:
    frames = tmp_path / "frames"
    frames.mkdir()
    for index in range(215):
        Image.new("RGB", (14, 8)).save(frames / f"frame_{index:03d}.png")

    with pytest.raises(CalendarAnimError, match="frame_215.png"):
        inspect_final_frames(frames, (14, 8), frame_count=216)
