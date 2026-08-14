from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from PIL import Image

from calendar_anim.calendar.capture.final_media import FFmpegTools
from calendar_anim.calendar.cayde_216.capture import (
    CAYDE_216_STABILIZATION_SECONDS,
    PREVIEW_HUMAN_FRAMES,
    _parse_preview_frames,
    _validate_cayde_216_preview_gate,
)
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
    SOURCE_RUN_ID,
)
from calendar_anim.calendar.cayde_216.toolbar_composition import (
    compose_calendar_toolbar_frame,
)
from calendar_anim.calendar.cayde_216.upload import UPLOAD_ARTIFACT_NAMES, upload_store
from calendar_anim.calendar.cayde_216.window_search import find_clean_windows
from calendar_anim.calendar.frame_mapping.colors import calendar_palette_color, contrast_ratio
from calendar_anim.calendar.hybrid_capture.artifacts import (
    AccountBSingleCaptureStore,
    HybridCaptureStore,
)
from calendar_anim.calendar.hybrid_capture.media import (
    FinalVisualProbe,
    build_final_visual_command,
    inspect_final_frames,
    validate_final_visual_probe,
)
from calendar_anim.calendar.hybrid_capture.models import (
    HybridCapturePlan,
    HybridFramePlan,
    HybridOutputMode,
    SingleProfilePreviewFrameResult,
    SingleProfilePreviewReport,
)
from calendar_anim.calendar.hybrid_capture.service import HybridCaptureService
from calendar_anim.calendar.models import CalendarRangeEvent
from calendar_anim.calendar.palette_presets import CAYDE_216_CANDIDATES, CAYDE_FINAL
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
        "palette_preset": "cayde-cyan-magenta",
        "background_color_id": "7",
        "foreground_color_ids": ["3", "5", "9", "11"],
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
    assert date(2030, 5, 5) == FIRST_WEEK
    assert FRAME_COUNT / FPS == 36.0
    assert weeks[-1] == date(2034, 6, 18)
    assert all(
        right - left == timedelta(days=7) for left, right in zip(weeks, weeks[1:], strict=False)
    )
    assert not set(weeks) & {date(2027, 10, 10) + timedelta(weeks=index) for index in range(108)}
    assert RUN_ID == "cayde-final-216f-6fps-cyan-magenta-rdate-126x72-36s-01"
    assert SOURCE_RUN_ID == "cayde-final-216f-6fps-rdate-126x72-36s-01"
    assert RUN_ID != SOURCE_RUN_ID


def test_cayde_216_single_profile_capture_contract_accepts_all_216_frames() -> None:
    frames = [
        HybridFramePlan(
            frame_index=index,
            human_frame=index + 1,
            week_start=FIRST_WEEK + timedelta(weeks=index),
            calendar_profile="account-b",
            calendar_name="Calendar Animation Lab B",
            capture_zoom_percent=90,
            expected_occurrences=1,
            source_frame_plan=f"frame-{index:04d}.json",
        )
        for index in range(216)
    ]

    plan = HybridCapturePlan(
        capture_strategy="single-profile-account-b",
        run_id=RUN_ID,
        source_run_id=SOURCE_RUN_ID,
        source_sha256="a" * 64,
        frame_count=216,
        fps=6,
        frames=frames,
    )

    assert plan.frame_count == 216
    assert plan.frames[-1].human_frame == 216
    with pytest.raises(ValueError, match="hybrid profile capture remains locked"):
        HybridCapturePlan(
            capture_strategy="hybrid",
            run_id=RUN_ID,
            source_run_id=SOURCE_RUN_ID,
            source_sha256="a" * 64,
            frame_count=216,
            fps=6,
            frames=frames,
        )


def test_cayde_216_final_sequence_validates_all_216_files(tmp_path: Path) -> None:
    frames = [
        HybridFramePlan(
            frame_index=index,
            human_frame=index + 1,
            week_start=FIRST_WEEK + timedelta(weeks=index),
            calendar_profile="account-b",
            calendar_name="Calendar Animation Lab B",
            capture_zoom_percent=90,
            expected_occurrences=1,
            source_frame_plan=f"frame-{index:04d}.json",
        )
        for index in range(FRAME_COUNT)
    ]
    plan = HybridCapturePlan(
        capture_strategy="single-profile-account-b",
        run_id=RUN_ID,
        source_run_id=SOURCE_RUN_ID,
        source_sha256="a" * 64,
        frame_count=FRAME_COUNT,
        fps=FPS,
        frames=frames,
    )
    store = HybridCaptureStore(tmp_path / "216-runs")
    resolution = (1, 1)
    for frame in frames:
        path = store.final_frame_path(
            RUN_ID, frame.frame_index, HybridOutputMode.HEADER_PRESERVED_FILL, resolution
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", resolution, "black").save(path)

    HybridCaptureService(store, lambda *_: None)._validate_final_sequence(
        plan, HybridOutputMode.HEADER_PRESERVED_FILL, resolution
    )

    store.final_frame_path(
        RUN_ID, FRAME_COUNT - 1, HybridOutputMode.HEADER_PRESERVED_FILL, resolution
    ).unlink()
    with pytest.raises(CalendarAnimError, match="gap or duplicate"):
        HybridCaptureService(store, lambda *_: None)._validate_final_sequence(
            plan, HybridOutputMode.HEADER_PRESERVED_FILL, resolution
        )


def test_cayde_216_full_capture_requires_passed_five_frame_preview(tmp_path: Path) -> None:
    store = AccountBSingleCaptureStore(tmp_path / "216-runs")
    results = []
    for human_frame in PREVIEW_HUMAN_FRAMES:
        frame_index = human_frame - 1
        output = store.preview_frame_path(RUN_ID, frame_index)
        output.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (1512, 864), "black").save(output)
        week = FIRST_WEEK + timedelta(weeks=frame_index)
        results.append(
            SingleProfilePreviewFrameResult(
                human_frame=human_frame,
                frame_index=frame_index,
                expected_week=week,
                visible_week=week,
                week_validation="PASS",
                output=str(output),
                output_size=(1512, 864),
                header_present=True,
                left_time_gutter_present=True,
                timezone_label_present=True,
                create_button_excluded=True,
                pre_06_blank_gap_present=False,
                vertical_interval="06:00-00:00",
                capture="PASS",
                native_browser_viewport={},
                native_composed_crop_dimensions=(1512, 864),
                header_source_rect=[0, 0, 1, 1],
                time_gutter_source_rect=[0, 0, 1, 1],
                grid_source_rect=[0, 0, 1, 1],
                header_output_rect=[0, 0, 1, 1],
                time_gutter_output_rect=[0, 0, 1, 1],
                grid_output_rect=[0, 0, 1, 1],
            )
        )
    report = SingleProfilePreviewReport(
        run_id=RUN_ID,
        frames=results,
        geometry_consistent=True,
        preview="PASS",
    )
    report_path = store.preview_report_path(RUN_ID)
    report_path.write_text(report.model_dump_json(indent=2) + "\n", encoding="utf-8")

    assert _validate_cayde_216_preview_gate(store, RUN_ID) == report

    report.geometry_consistent = False
    report_path.write_text(report.model_dump_json(indent=2) + "\n", encoding="utf-8")
    with pytest.raises(CalendarAnimError, match="preview gate is not PASS"):
        _validate_cayde_216_preview_gate(store, RUN_ID)


def test_cayde_216_preview_frames_are_locked_to_five_approved_samples() -> None:
    assert _parse_preview_frames("1,54,108,162,216") == list(PREVIEW_HUMAN_FRAMES)
    with pytest.raises(CalendarAnimError, match="sanity frames"):
        _parse_preview_frames("1,54,108,216")


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


def test_cayde_216_palette_candidates_are_isolated_and_improve_separation() -> None:
    assert CAYDE_FINAL.background_color_id == "1"
    assert "1" in CAYDE_FINAL.foreground_color_ids
    assert len(CAYDE_216_CANDIDATES) == 3
    for candidate in CAYDE_216_CANDIDATES:
        assert candidate.background_color_id not in candidate.foreground_color_ids
        background = calendar_palette_color(candidate.background_color_id)
        ratios = [
            contrast_ratio(background.hex, calendar_palette_color(color_id).hex)
            for color_id in candidate.foreground_color_ids
        ]
        assert min(ratios) >= 1.75


def test_cayde_216_window_search_skips_conflicts_and_returns_disjoint_ranges() -> None:
    zone = ZoneInfo("America/Sao_Paulo")
    conflict_week = FIRST_WEEK + timedelta(weeks=10)
    events = [
        CalendarRangeEvent(
            id="existing",
            start=datetime.combine(conflict_week, datetime.min.time(), zone),
            end=datetime.combine(conflict_week + timedelta(days=1), datetime.min.time(), zone),
        )
    ]

    candidates = find_clean_windows(
        events,
        search_start=FIRST_WEEK,
        search_end_exclusive=FIRST_WEEK + timedelta(weeks=700),
        timezone="America/Sao_Paulo",
    )

    assert [candidate.first_week for candidate in candidates] == [
        conflict_week + timedelta(weeks=1),
        conflict_week + timedelta(weeks=217),
    ]
    assert candidates[0].end_exclusive == candidates[1].first_week


def test_cayde_216_upload_store_is_namespaced_and_requires_final_gate_artifacts() -> None:
    store = upload_store()

    assert store.plan_root == Path("output/216-plans")
    assert store.state_root == Path("output/216-runs")
    assert store.recurrence_plan_name == "recurrence-plan.json"
    assert UPLOAD_ARTIFACT_NAMES == (
        "animation-plan.json",
        "recurrence-plan.json",
        "recurrence-report.json",
        "sizing-report.json",
        "remote-preflight.json",
    )
    assert store.state_path(RUN_ID) == (
        Path("output/216-runs") / RUN_ID / "account-b-upload-state.json"
    )


def test_cayde_216_capture_uses_slower_calendar_visual_stabilization() -> None:
    assert CAYDE_216_STABILIZATION_SECONDS == 5.0


def test_calendar_toolbar_composition_preserves_segments_and_final_geometry(
    tmp_path: Path,
) -> None:
    raw = tmp_path / "raw.png"
    header_grid = tmp_path / "header-grid.png"
    output = tmp_path / "preview.png"
    toolbar = tmp_path / "toolbar.png"
    raw_image = Image.new("RGB", (1920, 1080), "#101010")
    for y in range(58):
        for x in range(1841):
            raw_image.putpixel((x, y), (255, 0, 0))
    raw_image.save(raw)
    raw_image.close()
    source = Image.new("RGB", (1841, 852), (255, 0, 255))
    for y in range(75):
        for x in range(1841):
            source.putpixel((x, y), (0, 255, 0))
    for y in range(75, 852):
        for x in range(73):
            source.putpixel((x, y), (0, 0, 255))
    source.save(header_grid)
    source.close()

    metrics = compose_calendar_toolbar_frame(raw, header_grid, output, toolbar_artifact=toolbar)

    assert metrics["native_composite_dimensions"] == [1920, 910]
    assert metrics["output_toolbar_rect"] == [0, 0, 1512, 55]
    assert metrics["output_week_header_rect"] == [0, 55, 1512, 71]
    assert metrics["output_time_gutter_rect"] == [0, 126, 60, 738]
    assert metrics["output_event_grid_rect"] == [60, 126, 1452, 738]
    assert metrics["event_grid_resampling"] == "nearest-neighbor"
    with Image.open(output) as image:
        assert image.size == (1512, 864)
        assert image.getpixel((756, 20))[0] > 240
        assert image.getpixel((756, 90))[1] > 240
        assert image.getpixel((20, 300))[2] > 240
        assert image.getpixel((800, 300))[0] > 240
        assert image.getpixel((800, 300))[2] > 240
    with Image.open(toolbar) as image:
        assert image.size == (1920, 58)
