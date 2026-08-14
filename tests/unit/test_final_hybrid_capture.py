from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace, TracebackType

import pytest
from PIL import Image

from calendar_anim.browser.playwright_gateway import (
    CaptureReadinessError,
    EventPopulationAudit,
    deduplicate_event_records,
    header_time_gutter_grid_clips,
    logical_grid_clip,
    structural_grid_bounds_from_diagnostics,
    wait_for_stable_population,
    wait_for_stable_visual_grid,
)
from calendar_anim.calendar.capture.final_media import (
    AVMediaProbe,
    FFmpegTools,
    build_extract_audio_command,
    frame_sequence_duration,
)
from calendar_anim.calendar.frame_mapping.models import (
    CalendarMappedCell,
    CellRole,
    EventCompressionMode,
    FrameMappingMode,
    FrameMappingStatistics,
    SingleFrameCalendarPlan,
)
from calendar_anim.calendar.hybrid_capture import commands as hybrid_commands
from calendar_anim.calendar.hybrid_capture.artifacts import (
    HIGH_RESOLUTION,
    AccountBSingleCaptureStore,
    HybridCaptureStore,
    compose_output_mode,
    normalize_grid,
    parse_output_resolution,
)
from calendar_anim.calendar.hybrid_capture.media import (
    FinalVisualProbe,
    build_final_visual_command,
    inspect_final_frames,
    validate_final_frames,
    validate_final_visual_probe,
)
from calendar_anim.calendar.hybrid_capture.models import (
    CURRENT_CAPTURE_IMPLEMENTATION_VERSION,
    CURRENT_PROFILE_NAVIGATION_VERSION,
    HybridCapturePlan,
    HybridFramePlan,
    HybridFrameStatus,
    HybridOutputMode,
    HybridSanityReport,
    SanityFrameResult,
)
from calendar_anim.calendar.hybrid_capture.service import (
    HybridCaptureService,
    final_sanity_allows_capture,
    final_sanity_gate_status,
    image_has_expected_visual_occupancy,
)
from calendar_anim.calendar.subcolumn_ordering import SubcolumnOrderStrategy
from calendar_anim.exceptions import CalendarAnimError


def _hybrid_plan(tmp_path: Path) -> HybridCapturePlan:
    frames = []
    for index in range(108):
        is_a = index <= 22
        frames.append(
            HybridFramePlan(
                frame_index=index,
                human_frame=index + 1,
                week_start=date(2028, 1, 2),
                calendar_profile="account-a" if is_a else "account-b",
                calendar_name="Calendar Animation Lab" if is_a else "Calendar Animation Lab B",
                capture_zoom_percent=33 if is_a else 90,
                expected_occurrences=1,
                source_frame_plan=str(tmp_path / f"frame-{index:04d}.json"),
            )
        )
    return HybridCapturePlan(
        run_id="hybrid-test",
        source_run_id="source-test",
        source_sha256="a" * 64,
        frames=frames,
    )


def test_hybrid_boundary_and_final_filenames_are_exact(tmp_path: Path) -> None:
    plan = _hybrid_plan(tmp_path)
    store = HybridCaptureStore(tmp_path / "runs")

    assert plan.frames[22].human_frame == 23
    assert plan.frames[22].calendar_profile == "account-a"
    assert plan.frames[22].capture_zoom_percent == 33
    assert plan.frames[23].human_frame == 24
    assert plan.frames[23].calendar_profile == "account-b"
    assert plan.frames[23].capture_zoom_percent == 90
    paths = [store.final_frame_path(plan.run_id, index) for index in range(108)]
    assert len(paths) == len(set(paths)) == 108
    assert paths[0].name == "frame_000.png"
    assert paths[-1].name == "frame_107.png"


def test_normalization_is_exact_504x288_nearest_neighbor(tmp_path: Path) -> None:
    source = tmp_path / "logical.png"
    output = tmp_path / "normalized.png"
    image = Image.new("RGB", (126, 72), "#7986CB")
    image.putpixel((125, 71), (171, 71, 188))
    image.save(source)
    image.close()

    normalize_grid(source, output)

    with Image.open(output) as normalized:
        assert normalized.size == (504, 288)
        assert normalized.getpixel((503, 287)) == (171, 71, 188)


def test_visual_readiness_rejects_empty_expected_capture(tmp_path: Path) -> None:
    empty = tmp_path / "empty.png"
    occupied = tmp_path / "occupied.png"
    Image.new("RGB", (200, 100), "#202124").save(empty)
    image = Image.new("RGB", (200, 100), "#202124")
    for x in range(20, 180):
        for y in range(20, 80):
            image.putpixel((x, y), (121, 134, 203))
    image.save(occupied)
    image.close()

    assert image_has_expected_visual_occupancy(empty, 100) is False
    assert image_has_expected_visual_occupancy(occupied, 100) is True
    assert image_has_expected_visual_occupancy(empty, 0) is True


def test_composed_capture_retries_empty_expected_content_before_returning(
    tmp_path: Path,
) -> None:
    class EmptyThenOccupiedGateway(ReadOnlyFakeGateway):
        def __init__(self) -> None:
            super().__init__()
            self.logical_captures = 0

        def capture_logical_event_grid(self, output_path: Path) -> dict[str, object]:
            self.logical_captures += 1
            if self.logical_captures < 3:
                output_path.parent.mkdir(parents=True, exist_ok=True)
                Image.new("RGB", (126, 72), "#202124").save(output_path)
                return {
                    "event_count": 0,
                    "rendered_color_counts": {},
                    "logical_cell_width": 4.0,
                    "logical_cell_height": 4.0,
                    "logical_clip": {
                        "x": 0.0,
                        "y": 0.0,
                        "width": 504.0,
                        "height": 288.0,
                    },
                }
            return super().capture_logical_event_grid(output_path)

    gateway = EmptyThenOccupiedGateway()
    service = HybridCaptureService(HybridCaptureStore(tmp_path / "runs"), lambda *_: gateway)
    frame = _hybrid_plan(tmp_path).frames[0]

    metrics = service._capture_composed_frame(
        gateway,
        frame,
        tmp_path / "raw.png",
        tmp_path / "logical.png",
        tmp_path / "header.png",
        tmp_path / "output.png",
        HybridOutputMode.HEADER_PRESERVED_FILL,
        (1512, 864),
        tmp_path / "debug",
    )

    assert gateway.logical_captures == 3
    assert metrics["visual_content_occupancy"] is True
    assert metrics["visual_content_occupancy_attempt"] == 3


@pytest.mark.parametrize(
    ("mode", "letterbox", "stretch", "header"),
    [
        (HybridOutputMode.PIXEL_FAITHFUL, False, False, False),
        (HybridOutputMode.HEADER_PRESERVED_LETTERBOX, True, False, True),
        (HybridOutputMode.HEADER_PRESERVED_FILL, False, True, True),
    ],
)
def test_output_modes_have_explicit_geometry_tradeoffs(
    tmp_path: Path,
    mode: HybridOutputMode,
    letterbox: bool,
    stretch: bool,
    header: bool,
) -> None:
    logical = tmp_path / "logical.png"
    header_source = tmp_path / "header.png"
    output = tmp_path / f"{mode.value}.png"
    Image.new("RGB", (126, 72), "#7986CB").save(logical)
    Image.new("RGB", (126, 90), "#7986CB").save(header_source)

    report = compose_output_mode(logical, header_source, output, mode)

    with Image.open(output) as image:
        assert image.size == (504, 288)
    assert report["letterbox"] is letterbox
    assert report["stretch"] is stretch
    assert report["header_included"] is header
    assert report["vertical_interval"] == "06:00-00:00"
    assert report["resampling"] == "nearest-neighbor"
    assert report["blur_or_sharpen"] is False


def test_pixel_faithful_matches_current_logical_grid_normalization(tmp_path: Path) -> None:
    source = tmp_path / "real-debug-proportions.png"
    output = tmp_path / "output.png"
    Image.new("RGB", (1768, 777), "#7986CB").save(source)

    report = compose_output_mode(
        source,
        None,
        output,
        HybridOutputMode.PIXEL_FAITHFUL,
    )

    assert report["logical_grid_normalization"] is True
    assert report["stretch"] is False
    with Image.open(output) as image:
        assert image.size == (504, 288)


def test_mode_directories_and_checkpoints_are_isolated(tmp_path: Path) -> None:
    plan = _hybrid_plan(tmp_path)
    store = HybridCaptureStore(tmp_path / "runs")
    states = {mode: store.initialize_state(plan, mode) for mode in HybridOutputMode}

    assert len({store.state_path(plan.run_id, mode) for mode in HybridOutputMode}) == 3
    assert len({store.final_frames_directory(plan.run_id, mode) for mode in HybridOutputMode}) == 3
    assert {state.output_mode for state in states.values()} == set(HybridOutputMode)

    high_resolution = store.initialize_state(
        plan, HybridOutputMode.HEADER_PRESERVED_FILL, HIGH_RESOLUTION
    )
    assert high_resolution.output_width == 1512
    assert high_resolution.output_height == 864
    assert store.state_path(
        plan.run_id, HybridOutputMode.HEADER_PRESERVED_FILL, HIGH_RESOLUTION
    ) != store.state_path(plan.run_id, HybridOutputMode.HEADER_PRESERVED_FILL)
    assert (
        store.final_frames_directory(
            plan.run_id, HybridOutputMode.HEADER_PRESERVED_FILL, HIGH_RESOLUTION
        ).parts[-1]
        == "1512x864"
    )


def test_resolution_parser_preserves_required_seven_by_four_aspect() -> None:
    assert parse_output_resolution("1512x864") == (1512, 864)
    assert parse_output_resolution("504X288") == (504, 288)
    with pytest.raises(CalendarAnimError, match="7:4"):
        parse_output_resolution("1920x1080")


def test_final_visual_command_uses_all_frames_in_order_and_approved_codec(tmp_path: Path) -> None:
    tools = FFmpegTools(Path("ffmpeg"), Path("ffprobe"), "test")
    command = build_final_visual_command(tools, tmp_path / "frames", tmp_path / "visual.mp4")

    assert command[command.index("-framerate") + 1] == "3"
    assert command[command.index("-start_number") + 1] == "0"
    assert command[command.index("-frames:v") + 1] == "108"
    assert command[command.index("-i") + 1].endswith("frame_%03d.png")
    assert command[command.index("-profile:v") + 1] == "high"
    assert command[command.index("-crf") + 1] == "10"
    assert command[command.index("-preset") + 1] == "slow"
    assert command[command.index("-pix_fmt") + 1] == "yuv420p"
    assert command[command.index("-vf") + 1] == "setsar=1"
    assert frame_sequence_duration(108, 3) == 36


def _write_final_png_sequence(
    directory: Path,
    *,
    resolution: tuple[int, int] = (14, 8),
    omit: int | None = None,
) -> None:
    directory.mkdir(parents=True)
    for index in range(108):
        if index != omit:
            Image.new("RGB", resolution, (index, 0, 0)).save(directory / f"frame_{index:03d}.png")


def test_single_profile_composer_resolves_and_accepts_exact_sequence(tmp_path: Path) -> None:
    store = AccountBSingleCaptureStore(tmp_path / "runs")
    directory = store.final_frames_directory(
        "run", HybridOutputMode.HEADER_PRESERVED_FILL, (1512, 864)
    )
    assert directory == (
        tmp_path
        / "runs"
        / "run"
        / "single-profile-final-frames"
        / "header-preserved-fill"
        / "1512x864"
    )
    _write_final_png_sequence(directory)

    sequence = inspect_final_frames(directory, (14, 8))

    assert sequence.count == 108
    assert sequence.first.name == "frame_000.png"
    assert sequence.last.name == "frame_107.png"
    assert [path.name for path in sequence.paths] == [
        f"frame_{index:03d}.png" for index in range(108)
    ]
    assert store.single_profile_final_directory(
        "run", HybridOutputMode.HEADER_PRESERVED_FILL, (1512, 864)
    ) == (
        tmp_path
        / "runs"
        / "run"
        / "final"
        / "single-profile"
        / "header-preserved-fill"
        / "1512x864"
    )


def test_single_profile_composer_reports_missing_frame(tmp_path: Path) -> None:
    directory = tmp_path / "frames"
    _write_final_png_sequence(directory, omit=37)

    with pytest.raises(CalendarAnimError, match=r"missing: frame_037\.png"):
        inspect_final_frames(directory, (14, 8))


def test_single_profile_composer_rejects_unexpected_png(tmp_path: Path) -> None:
    directory = tmp_path / "frames"
    _write_final_png_sequence(directory)
    Image.new("RGB", (14, 8)).save(directory / "contact-sheet.png")

    with pytest.raises(CalendarAnimError, match=r"unexpected: contact-sheet\.png"):
        inspect_final_frames(directory, (14, 8))


def test_single_profile_composer_rejects_wrong_resolution(tmp_path: Path) -> None:
    directory = tmp_path / "frames"
    _write_final_png_sequence(directory)

    with pytest.raises(CalendarAnimError, match="Final frame is not 28x16"):
        inspect_final_frames(directory, (28, 16))


def test_final_visual_probe_requires_exact_approved_media_properties() -> None:
    probe = FinalVisualProbe(
        codec="h264",
        profile="High",
        width=1512,
        height=864,
        fps=3.0,
        frame_count=108,
        duration_seconds=36.0,
        sample_aspect_ratio="1:1",
    )

    validate_final_visual_probe(probe, (1512, 864))

    with pytest.raises(CalendarAnimError, match="frames=107"):
        validate_final_visual_probe(
            FinalVisualProbe(**{**probe.__dict__, "frame_count": 107}), (1512, 864)
        )


def test_single_profile_compose_command_is_media_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = AccountBSingleCaptureStore(tmp_path / "runs")
    hybrid = _hybrid_plan(tmp_path)
    plan = HybridCapturePlan(
        **{
            **hybrid.model_dump(exclude={"frames", "capture_strategy"}),
            "capture_strategy": "single-profile-account-b",
            "frames": [
                frame.model_copy(
                    update={"calendar_profile": "account-b", "capture_zoom_percent": 90}
                )
                for frame in hybrid.frames
            ],
        }
    )
    store.save_plan(plan)
    frame_directory = store.final_frames_directory(
        plan.run_id, HybridOutputMode.HEADER_PRESERVED_FILL, (14, 8)
    )
    _write_final_png_sequence(frame_directory)
    checkpoint = store.state_path(plan.run_id, HybridOutputMode.HEADER_PRESERVED_FILL, (14, 8))
    checkpoint.parent.mkdir(parents=True)
    checkpoint.write_text("trusted checkpoint", encoding="utf-8")
    probe = FinalVisualProbe("h264", "High", 14, 8, 3.0, 108, 36.0, "1:1")

    monkeypatch.setattr(hybrid_commands, "AccountBSingleCaptureStore", lambda: store)
    monkeypatch.setattr(
        hybrid_commands,
        "detect_ffmpeg",
        lambda: FFmpegTools(Path("ffmpeg"), Path("ffprobe"), "ffmpeg test"),
    )

    def fake_compose(
        tools: FFmpegTools,
        source: Path,
        output: Path,
        resolution: tuple[int, int],
    ) -> Path:
        assert source == frame_directory
        assert resolution == (14, 8)
        output.parent.mkdir(parents=True)
        output.write_bytes(b"visual only")
        return output

    monkeypatch.setattr(hybrid_commands, "compose_final_visual", fake_compose)
    monkeypatch.setattr(hybrid_commands, "probe_final_visual", lambda tools, output: probe)
    monkeypatch.setattr(
        hybrid_commands,
        "_gateway_factory",
        lambda *args, **kwargs: pytest.fail("composer must not open a browser"),
    )

    hybrid_commands.compose_final_single_profile_command(
        plan.run_id, HybridOutputMode.HEADER_PRESERVED_FILL, "14x8"
    )

    assert checkpoint.read_text(encoding="utf-8") == "trusted checkpoint"
    final = store.single_profile_final_directory(
        plan.run_id, HybridOutputMode.HEADER_PRESERVED_FILL, (14, 8)
    )
    assert (final / "final-video-no-audio.mp4").read_bytes() == b"visual only"
    report = store.load_json_report(final / "visual-composition-report.json")
    assert report["capture_checkpoint_touched"] is False
    assert report["calendar_touched"] is False
    assert report["recurrence_touched"] is False
    assert report["browser_opened"] is False


def test_single_profile_audio_mux_uses_existing_visual_without_capture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = AccountBSingleCaptureStore(tmp_path / "runs")
    run_id = "mux-test"
    mode = HybridOutputMode.HEADER_PRESERVED_FILL
    resolution = (14, 8)
    final = store.single_profile_final_directory(run_id, mode, resolution)
    final.mkdir(parents=True)
    visual = final / "final-video-no-audio.mp4"
    visual.write_bytes(b"trusted visual")
    source = tmp_path / "input.mp4"
    source.write_bytes(b"source with audio")
    checkpoint = store.state_path(run_id, mode, resolution)
    checkpoint.parent.mkdir(parents=True)
    checkpoint.write_text("trusted checkpoint", encoding="utf-8")
    png = store.final_frames_directory(run_id, mode, resolution) / "frame_000.png"
    png.parent.mkdir(parents=True)
    png.write_bytes(b"trusted png")
    visual_probe = FinalVisualProbe("h264", "High", 14, 8, 3.0, 108, 36.0, "1:1")
    av_probe = AVMediaProbe("h264", "aac", 14, 8, 3.0, 108, 36.0, 36.02, 36.02, "1:1")

    monkeypatch.setattr(hybrid_commands, "AccountBSingleCaptureStore", lambda: store)
    monkeypatch.setattr(hybrid_commands, "validate_input_hash", lambda path: None)
    monkeypatch.setattr(
        hybrid_commands,
        "detect_ffmpeg",
        lambda: FFmpegTools(Path("ffmpeg"), Path("ffprobe"), "ffmpeg test"),
    )
    monkeypatch.setattr(hybrid_commands, "probe_final_visual", lambda tools, path: visual_probe)
    monkeypatch.setattr(hybrid_commands, "probe_audio_codec", lambda tools, path: "aac")

    def fake_extract(
        tools: FFmpegTools, source_path: Path, output: Path, start: float, end: float
    ) -> Path:
        assert (source_path, start, end) == (source, 114.0, 150.0)
        output.write_bytes(b"exact aac")
        return output

    def fake_mux(tools: FFmpegTools, visual_path: Path, audio: Path, output: Path) -> Path:
        assert visual_path == visual
        output.write_bytes(b"copied video plus audio")
        return output

    monkeypatch.setattr(hybrid_commands, "extract_exact_audio", fake_extract)
    monkeypatch.setattr(hybrid_commands, "mux_audio", fake_mux)
    monkeypatch.setattr(hybrid_commands, "probe_av_media", lambda tools, path: av_probe)
    monkeypatch.setattr(
        hybrid_commands,
        "compose_final_visual",
        lambda *args, **kwargs: pytest.fail("mux must not recompose video"),
    )
    monkeypatch.setattr(
        hybrid_commands,
        "_gateway_factory",
        lambda *args, **kwargs: pytest.fail("mux must not open a browser"),
    )

    hybrid_commands.mux_final_single_profile_audio_command(run_id, mode, "14x8", source)

    assert visual.read_bytes() == b"trusted visual"
    assert checkpoint.read_text(encoding="utf-8") == "trusted checkpoint"
    assert png.read_bytes() == b"trusted png"
    report = store.load_json_report(final / "single-profile-audio-mux-report.json")
    assert report["video_copied"] is True
    assert report["video_reencoded"] is False
    assert report["calendar_reads"] is False
    assert report["calendar_writes"] is False
    assert report["recurrence_touched"] is False
    assert report["browser_opened"] is False


def test_single_profile_audio_mux_reports_missing_silent_video(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    store = AccountBSingleCaptureStore(tmp_path / "runs")
    monkeypatch.setattr(hybrid_commands, "AccountBSingleCaptureStore", lambda: store)
    monkeypatch.setattr(hybrid_commands, "validate_input_hash", lambda path: None)

    with pytest.raises(hybrid_commands.typer.Exit):
        hybrid_commands.mux_final_single_profile_audio_command(
            "missing", HybridOutputMode.HEADER_PRESERVED_FILL, "14x8", tmp_path / "input.mp4"
        )

    assert "Single-profile silent MP4 does not exist; expected:" in capsys.readouterr().err


def test_audio_source_is_exact_114_to_150(tmp_path: Path) -> None:
    command = build_extract_audio_command(
        FFmpegTools(Path("ffmpeg"), Path("ffprobe"), "test"),
        Path("input.mp4"),
        tmp_path / "audio.m4a",
        114,
        150,
        copy_aac=True,
    )

    assert command[command.index("-ss") + 1] == "114.000000"
    assert command[command.index("-t") + 1] == "36.000000"
    assert command[command.index("-c:a") + 1] == "copy"


class ReadOnlyFakeGateway:
    def __init__(self, zoom_applied: float = 90.0) -> None:
        self.opened: list[date] = []
        self.waited: list[tuple[date, int]] = []
        self.population_waits: list[int] = []
        self.reloads: list[date] = []
        self.zoom_applied = zoom_applied

    def __enter__(self) -> "ReadOnlyFakeGateway":
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        return None

    def open_week(self, week_start: date) -> None:
        self.opened.append(week_start)

    def wait_until_ready(self, week_start: date, minimum_event_count: int) -> None:
        self.waited.append((week_start, minimum_event_count))

    def wait_for_animation_events(self, expected_count: int) -> object:
        self.population_waits.append(expected_count)
        return object()

    def reload_current_week(self, week_start: date, minimum_event_count: int) -> None:
        self.reloads.append(week_start)

    def capture_debug_state(self) -> dict[str, object]:
        return {
            "url": "https://calendar.google.com/calendar/u/0/r/week/2028/1/2",
            "viewport": {"width": 1920, "height": 1080},
            "scroll_position": {"scrollTop": 360.0, "targetScrollTop": 360.0},
            "grid_diagnostics": {"strategy": "css-grid-seven-tracks"},
        }

    def inspect_navigation(self, expected_week: date) -> dict[str, object]:
        return {
            "state": "ready",
            "week_matches": True,
            "logged_in_detection": True,
            "calendar_shell_detection": True,
            "week_view_detection": True,
            "visible_week_date": expected_week.isoformat(),
            "current_url": (
                f"https://calendar.google.com/calendar/u/0/r/week/"
                f"{expected_week.year}/{expected_week.month}/{expected_week.day}"
            ),
            "browser_profile_path": ".calendar-anim/browser-profile",
            "zoom_applied": self.zoom_applied,
        }

    def capture_viewport(self, output_path: Path) -> None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (1920, 1080), "#202124").save(output_path)

    def capture_logical_event_grid(self, output_path: Path) -> dict[str, object]:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (126, 72), "#7986CB").save(output_path)
        return {
            "event_count": 1,
            "rendered_color_counts": {"rgb(121, 134, 203)": 1},
            "logical_cell_width": 4.0,
            "logical_cell_height": 4.0,
            "logical_clip": {"x": 0.0, "y": 0.0, "width": 504.0, "height": 288.0},
        }

    def capture_header_event_grid(self, output_path: Path) -> dict[str, object]:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        image = Image.new("RGB", (1841, 852), "#7986CB")
        for y in range(75):
            for x in range(1841):
                image.putpixel((x, y), (32, 33, 36))
        for y in range(75, 852):
            for x in range(73):
                image.putpixel((x, y), (32, 33, 36))
        image.save(output_path)
        image.close()
        return {
            "header_grid_bounds": {
                "header_clip": {"x": 0.0, "y": 0.0, "width": 1841.0, "height": 75.0},
                "time_gutter_clip": {
                    "x": 0.0,
                    "y": 75.0,
                    "width": 73.0,
                    "height": 777.0,
                },
                "event_grid_clip": {
                    "x": 73.0,
                    "y": 75.0,
                    "width": 1768.0,
                    "height": 777.0,
                },
                "composite_dimensions": [1841, 852],
                "native_header_height": 75,
                "native_time_gutter_width": 73,
                "native_grid_width": 1768,
                "native_grid_height": 777,
            },
            "header_included": True,
            "left_time_gutter_included": True,
            "timezone_label_included": True,
            "create_button_excluded": True,
            "vertical_interval": "06:00-00:00",
            "empty_pre_06_interval_removed": True,
        }


def _write_uniform_frame_plan(path: Path, frame_index: int) -> None:
    now = datetime.fromisoformat("2028-01-02T06:00:00-03:00")
    cells = [
        CalendarMappedCell(
            logical_x=x,
            logical_y=y,
            day_offset=x // 18,
            subcolumn=x % 18,
            start=now,
            end=datetime.fromisoformat("2028-01-02T06:15:00-03:00"),
            color_id="1",
            color_hex="#7986CB",
            cell_role=CellRole.BACKGROUND,
        )
        for y in range(72)
        for x in range(126)
    ]
    plan = SingleFrameCalendarPlan(
        animation_id="hybrid-test",
        run_id=f"hybrid-test-frame-{frame_index:04d}",
        frame_index=frame_index,
        timezone="America/Sao_Paulo",
        week_start_date=date(2028, 1, 2),
        source_grid_width=126,
        source_grid_height=72,
        target_grid_width=126,
        target_grid_height=72,
        columns_per_day=18,
        fit="contain",
        mapping_mode=FrameMappingMode.FULL_GRID,
        event_compression=EventCompressionMode.SYNCHRONIZED_HORIZONTAL_BANDS,
        palette_preset="cayde-final",
        profile_ready=True,
        horizontal_strategy="fixed",
        subcolumn_order_strategy=SubcolumnOrderStrategy.ZERO_WIDTH,
        max_execute_events=5200,
        statistics=FrameMappingStatistics(
            source_blocks=1,
            expanded_logical_cells=9072,
            non_background_cells=0,
            mapped_cells=9072,
            calendar_events=1,
            unique_calendar_colors=1,
            cells_per_event=9072,
            compression_ratio=1 / 9072,
        ),
        mapped_cells=cells,
        events=[],
    )
    path.write_text(plan.model_dump_json(), encoding="utf-8")


def test_sanity_capture_uses_only_read_browser_methods_and_builds_artifacts(
    tmp_path: Path,
) -> None:
    plan = _hybrid_plan(tmp_path)
    _write_uniform_frame_plan(Path(plan.frames[23].source_frame_plan), 23)
    gateway = ReadOnlyFakeGateway()
    store = HybridCaptureStore(tmp_path / "runs")
    service = HybridCaptureService(store, lambda _profile, _zoom: gateway)

    report = service.capture_sanity(plan, [24])

    assert report.automated_result == "PASS"
    assert report.google_calendar_writes is False
    assert gateway.opened == [date(2028, 1, 2)]
    assert gateway.waited == [(date(2028, 1, 2), 0)]
    assert gateway.population_waits == [1]
    sanity = store.sanity_directory(plan.run_id)
    assert (sanity / "frame-024/raw.png").is_file()
    assert (sanity / "frame-024/normalized.png").is_file()
    assert (sanity / "hybrid-sanity-contact-sheet.png").is_file()
    assert (sanity / "sanity-report.json").is_file()


def test_seam_uses_frame_23_a_and_frame_24_b_with_equal_normalized_geometry(
    tmp_path: Path,
) -> None:
    plan = _hybrid_plan(tmp_path)
    store = HybridCaptureStore(tmp_path / "runs")
    store.save_sanity_report(
        HybridSanityReport(
            run_id=plan.run_id,
            frames_checked=[24],
            results=[
                SanityFrameResult(
                    frame_index=23,
                    human_frame=24,
                    profile="account-b",
                    week_start=date(2028, 1, 2),
                    expected_occurrences=1,
                    rendered_dom_events=1,
                    capture_success=True,
                    normalized_width=504,
                    normalized_height=288,
                    logical_cell_width=4,
                    logical_cell_height=4,
                    expected_color_distribution={"#7986CB": 9072},
                    rendered_color_distribution={"rgb(121, 134, 203)": 1},
                    logical_cell_match_ratio=1,
                    obvious_missing_content=False,
                    obvious_color_mismatch=False,
                    obvious_ordering_issue=False,
                    raw_artifact="raw.png",
                    logical_artifact="logical.png",
                    normalized_artifact="normalized.png",
                    expected_artifact="expected.png",
                )
            ],
            automated_result="PASS",
        )
    )
    gateways: list[tuple[str, int, ReadOnlyFakeGateway]] = []

    def factory(profile: str, zoom: int) -> ReadOnlyFakeGateway:
        gateway = ReadOnlyFakeGateway()
        gateways.append((profile, zoom, gateway))
        return gateway

    report = HybridCaptureService(store, factory).capture_seam(plan)

    assert [(profile, zoom) for profile, zoom, _gateway in gateways] == [
        ("account-a", 33),
        ("account-b", 90),
    ]
    assert report.account_a_frame_index == 22
    assert report.account_b_frame_index == 23
    assert report.geometry_result == "PASS"
    assert report.google_calendar_writes is False
    assert (store.seam_directory(plan.run_id) / "a-b-transition-geometry.png").is_file()


def _dom_record(
    index: int,
    *,
    wrapper: bool = False,
    structural: bool = False,
    event_id: str | None = None,
) -> dict[str, object]:
    return {
        "raw_index": index,
        "contains_matching_descendant": wrapper,
        "matching_descendant_count": 1 if wrapper else 0,
        "data_eventid": event_id,
        "data_eventchip": None,
        "data_dragsource_type": "4" if structural else None,
        "href": None,
        "aria_label": f"event-{index}",
        "text": "",
        "visible_color": "rgb(121, 134, 203)",
        "x": float(index % 126) * 4,
        "y": float(index // 126) * 4,
        "width": 500.0 if structural else 4.0,
        "height": 4.0,
        "viewport_width": 1920.0,
        "viewport_height": 1080.0,
        "in_viewport": True,
    }


@pytest.mark.parametrize(("raw_count", "expected"), [(1945, 972), (901, 450)])
def test_dom_wrapper_chip_pairs_are_deduplicated(raw_count: int, expected: int) -> None:
    wrappers = [_dom_record(index, wrapper=True) for index in range(expected)]
    chips = [_dom_record(index, event_id=f"event-{index}") for index in range(expected)]
    records = [*wrappers, *chips, _dom_record(raw_count - 1, structural=True)]

    audit = deduplicate_event_records(records)

    assert len(records) == raw_count
    assert audit.raw_node_count == raw_count
    assert audit.wrapper_nodes_removed == expected
    assert audit.structural_nodes_removed == 1
    assert audit.unique_event_count == expected


class FakeClock:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        return self.value

    def sleep(self, seconds: float) -> None:
        self.value += seconds


def _audit(count: int) -> EventPopulationAudit:
    return EventPopulationAudit(
        raw_node_count=count * 2 + 1,
        leaf_node_count=count + 1,
        unique_event_count=count,
        identity_digest=f"digest-{count}",
        wrapper_nodes_removed=count,
        structural_nodes_removed=1,
        duplicate_leaf_nodes_removed=0,
        rendered_color_counts={"rgb(121, 134, 203)": count},
    )


def test_population_polling_records_dom_population_without_using_it_as_gate() -> None:
    clock = FakeClock()
    values = iter([1, 500, 972, 972, 972])

    result = wait_for_stable_population(
        lambda: (_audit(next(values)), 0.9, {"left": 72.0, "width": 1764.0}),
        expected_count=972,
        timeout_seconds=10,
        interval_seconds=1,
        stable_samples=3,
        expected_coordinate_scale=0.9,
        clock=clock,
        sleeper=clock.sleep,
    )

    assert result.unique_event_count == 972
    assert len(result.samples) == 3
    assert result.samples[-1]["stable_sequence"] == 3


def test_population_polling_does_not_require_an_exact_unique_count() -> None:
    clock = FakeClock()

    result = wait_for_stable_population(
        lambda: (_audit(970), 0.9, {"left": 72.0, "width": 1764.0}),
        expected_count=972,
        timeout_seconds=5,
        interval_seconds=1,
        stable_samples=3,
        expected_coordinate_scale=0.9,
        clock=clock,
        sleeper=clock.sleep,
    )

    assert result.unique_event_count == 970


def test_population_polling_times_out_only_when_layout_is_invalid() -> None:
    clock = FakeClock()

    with pytest.raises(CalendarAnimError, match="CAPTURE LOAD FAILURE") as captured:
        wait_for_stable_population(
            lambda: (_audit(1), 1.0, {"left": 72.0, "width": 1764.0}),
            expected_count=972,
            timeout_seconds=2,
            interval_seconds=1,
            stable_samples=3,
            expected_coordinate_scale=0.9,
            clock=clock,
            sleeper=clock.sleep,
        )

    assert isinstance(captured.value, CaptureReadinessError)
    assert len(captured.value.samples) == 3
    assert captured.value.samples[-1]["unique_event_chips"] == 1


def test_dom_population_below_75_percent_does_not_block_visual_capture() -> None:
    clock = FakeClock()

    result = wait_for_stable_visual_grid(
        lambda: (
            b"stable-grid",
            _audit(1800),
            0.9,
            {"left": 72.0, "right": 1836.0, "width": 1764.0},
            {"strategy": "css-grid-seven-tracks"},
        ),
        expected_count=3474,
        timeout_seconds=5,
        interval_seconds=1,
        stable_samples=3,
        expected_coordinate_scale=0.9,
        clock=clock,
        sleeper=clock.sleep,
    )

    assert result.unique_event_count == 1800
    assert result.samples[-1]["event_population_warning"] is True
    assert result.samples[-1]["stable_sequence"] == 3


def test_visual_grid_stability_requires_three_matching_samples() -> None:
    clock = FakeClock()
    images = iter([b"changing", b"stable", b"stable", b"stable"])

    result = wait_for_stable_visual_grid(
        lambda: (
            next(images),
            _audit(0),
            0.9,
            {"left": 72.0, "right": 1836.0, "width": 1764.0},
            {"strategy": "seven-equal-structural-columns"},
        ),
        expected_count=3474,
        timeout_seconds=5,
        interval_seconds=1,
        stable_samples=3,
        expected_coordinate_scale=0.9,
        clock=clock,
        sleeper=clock.sleep,
    )

    assert len(result.samples) == 4
    assert result.samples[-1]["stable_sequence"] == 3


def test_grid_can_be_selected_without_exactly_seven_day_nodes() -> None:
    diagnostics = {
        "selected": {"left": 72, "right": 1836, "width": 1764},
        "strategy": "css-grid-seven-tracks",
        "day_column_candidates": [],
        "selected_day_columns": [],
    }

    assert structural_grid_bounds_from_diagnostics(diagnostics) == {
        "left": 72.0,
        "right": 1836.0,
        "width": 1764.0,
    }


def test_missing_structural_grid_is_capture_load_failure() -> None:
    with pytest.raises(CalendarAnimError, match="content-independent Calendar week grid"):
        structural_grid_bounds_from_diagnostics({"selected": None, "day_column_candidates": []})


def test_structural_grid_clip_does_not_depend_on_sparse_or_dense_events() -> None:
    structural = {"left": 72.0, "right": 1836.0, "width": 1764.0}
    time_bounds = {"x": 0.0, "y": 300.0, "width": 1900.0, "height": 800.0}

    dense_clip = logical_grid_clip(structural, time_bounds, 0.9, 0.9)
    sparse_clip = logical_grid_clip(structural, time_bounds, 0.9, 0.9)

    assert dense_clip == sparse_clip
    assert dense_clip["width"] / 126 == pytest.approx(12.6)


def test_time_gutter_uses_structural_time_and_grid_bounds_only() -> None:
    header, body, gutter = header_time_gutter_grid_clips(
        {"x": 72.0, "y": 58.0, "width": 1768.0, "height": 75.0},
        {"x": 72.0, "y": 288.0, "width": 1768.0, "height": 777.0},
        {"x": 0.0, "y": 288.0, "width": 1855.0, "height": 777.0},
    )

    assert gutter == 72.0
    assert header == {"x": 0.0, "y": 58.0, "width": 1840.0, "height": 75.0}
    assert body == {"x": 0.0, "y": 288.0, "width": 1840.0, "height": 777.0}


def test_wrong_week_blocks_capture_before_screenshot(tmp_path: Path) -> None:
    plan = _hybrid_plan(tmp_path)
    _write_uniform_frame_plan(Path(plan.frames[23].source_frame_plan), 23)

    class WrongWeekGateway(ReadOnlyFakeGateway):
        def wait_until_ready(self, week_start: date, minimum_event_count: int) -> None:
            self.waited.append((week_start, minimum_event_count))
            raise CalendarAnimError("Calendar navigated to a different week")

        def reload_current_week(self, week_start: date, minimum_event_count: int) -> None:
            self.reloads.append(week_start)
            self.wait_until_ready(week_start, minimum_event_count)

    gateway = WrongWeekGateway()
    report = HybridCaptureService(
        HybridCaptureStore(tmp_path / "runs"), lambda _p, _z: gateway
    ).capture_sanity(plan, [24])

    assert report.automated_result == "CAPTURE ERROR"
    assert gateway.population_waits == []
    assert len(gateway.reloads) == 2


def test_previous_sanity_is_archived_before_replacement(tmp_path: Path) -> None:
    store = HybridCaptureStore(tmp_path / "runs")
    old = store.sanity_directory("run") / "sanity-report.json"
    old.parent.mkdir(parents=True)
    old.write_text("old", encoding="utf-8")

    archived = store.archive_sanity("run")

    assert archived is not None
    assert (archived / "sanity-report.json").read_text(encoding="utf-8") == "old"
    assert not store.sanity_directory("run").exists()


class RetryTwiceGateway(ReadOnlyFakeGateway):
    def wait_for_animation_events(self, expected_count: int) -> object:
        self.population_waits.append(expected_count)
        if len(self.population_waits) < 3:
            raise CalendarAnimError("CAPTURE LOAD FAILURE: transient DOM")
        return object()


def test_sanity_capture_reloads_twice_then_accepts_third_stable_attempt(
    tmp_path: Path,
) -> None:
    plan = _hybrid_plan(tmp_path)
    _write_uniform_frame_plan(Path(plan.frames[23].source_frame_plan), 23)
    gateway = RetryTwiceGateway()
    service = HybridCaptureService(HybridCaptureStore(tmp_path / "runs"), lambda _p, _z: gateway)

    report = service.capture_sanity(plan, [24])

    assert report.automated_result == "PASS"
    assert gateway.population_waits == [1, 1, 1]
    assert gateway.reloads == [date(2028, 1, 2), date(2028, 1, 2)]
    assert report.results[0].capture_retry_cycles == 2


class NeverReadyGateway(ReadOnlyFakeGateway):
    def wait_for_animation_events(self, expected_count: int) -> object:
        self.population_waits.append(expected_count)
        raise CalendarAnimError("CAPTURE LOAD FAILURE: only one DOM event")


def test_exhausted_loading_retries_are_capture_error_not_recurrence_no_go(
    tmp_path: Path,
) -> None:
    plan = _hybrid_plan(tmp_path)
    _write_uniform_frame_plan(Path(plan.frames[23].source_frame_plan), 23)
    gateway = NeverReadyGateway()
    service = HybridCaptureService(HybridCaptureStore(tmp_path / "runs"), lambda _p, _z: gateway)

    report = service.capture_sanity(plan, [24])

    assert report.automated_result == "CAPTURE ERROR"
    assert gateway.population_waits == [1, 1, 1]
    assert report.results[0].capture_load_success is False
    assert report.results[0].capture_retry_cycles == 2
    assert report.results[0].unique_event_population_valid is False


def test_low_dom_population_is_diagnostic_and_not_part_of_sanity_gate(
    tmp_path: Path,
) -> None:
    plan = _hybrid_plan(tmp_path)
    plan.frames[23].expected_occurrences = 10
    _write_uniform_frame_plan(Path(plan.frames[23].source_frame_plan), 23)
    gateway = ReadOnlyFakeGateway()

    report = HybridCaptureService(
        HybridCaptureStore(tmp_path / "runs"), lambda _p, _z: gateway
    ).capture_sanity(plan, [24])

    assert report.results[0].unique_event_population_valid is False
    assert report.results[0].obvious_missing_content is False
    assert report.automated_result == "PASS"


def test_one_frame_debug_capture_writes_all_artifacts(tmp_path: Path) -> None:
    plan = _hybrid_plan(tmp_path)
    gateway = ReadOnlyFakeGateway()
    store = HybridCaptureStore(tmp_path / "runs")

    report = HybridCaptureService(store, lambda _p, _z: gateway).capture_debug(
        plan, 60, "account-b"
    )

    directory = store.debug_frame_directory(plan.run_id, 60)
    assert report["google_calendar_writes"] is False
    assert (directory / "raw-browser.png").is_file()
    assert (directory / "grid-crop.png").is_file()
    assert (directory / "normalized.png").is_file()
    assert (directory / "debug.json").is_file()


def test_one_frame_mode_comparison_uses_one_read_only_browser_capture(
    tmp_path: Path,
) -> None:
    plan = _hybrid_plan(tmp_path)
    gateway = ReadOnlyFakeGateway()
    store = HybridCaptureStore(tmp_path / "runs")

    report = HybridCaptureService(store, lambda _p, _z: gateway).capture_debug_modes(
        plan, 60, "account-b"
    )

    directory = store.debug_frame_directory(plan.run_id, 60)
    assert gateway.opened == [date(2028, 1, 2)]
    assert report["google_calendar_writes"] is False
    assert set(report["modes"]) == {mode.value for mode in HybridOutputMode}  # type: ignore[arg-type]
    assert (directory / "raw-browser.png").is_file()
    assert (directory / "mode-a-pixel-faithful.png").is_file()
    assert (directory / "mode-b-header-preserved-letterbox.png").is_file()
    assert (directory / "mode-c-header-preserved-fill.png").is_file()
    assert (directory / "comparison-contact-sheet.png").is_file()
    assert (directory / "debug.json").is_file()
    assert not (directory / ".pixel-faithful-source.png").exists()
    assert not (directory / ".header-preserved-source.png").exists()


def test_high_resolution_debug_uses_native_crop_without_504_intermediate(
    tmp_path: Path,
) -> None:
    plan = _hybrid_plan(tmp_path)
    gateway = ReadOnlyFakeGateway()
    store = HybridCaptureStore(tmp_path / "runs")

    report = HybridCaptureService(store, lambda _p, _z: gateway).capture_debug_modes(
        plan, 60, "account-b", HIGH_RESOLUTION
    )

    directory = store.high_resolution_debug_directory(plan.run_id, 60)
    native = directory / "mode-c-native-crop.png"
    output = directory / "mode-c-1512x864.png"
    with Image.open(native) as image:
        assert image.size == (1841, 852)
        assert image.width > 504 and image.height > 288
    with Image.open(output) as image:
        assert image.size == HIGH_RESOLUTION
        assert image.width * 4 == image.height * 7
    modes = report["modes"]
    assert isinstance(modes, dict)
    mode = modes[HybridOutputMode.HEADER_PRESERVED_FILL.value]
    assert isinstance(mode, dict)
    assert mode["source_dimensions"] == [1841, 852]
    assert mode["final_dimensions"] == [1512, 864]
    assert mode["source_of_resize"] == "native browser crop"
    assert mode["intermediate_504x288"] is False
    assert mode["resize_passes"] == 1
    assert mode["header_resample_method"] == "lanczos"
    assert mode["grid_resample_method"] == "nearest-neighbor"
    assert mode["header_included"] is True
    assert report["pre_06_gap_present"] is False
    assert report["vertical_interval"] == "06:00-00:00"
    assert report["logical_grid"] == [126, 72]
    assert (directory / "comparison-hires.png").is_file()


def test_high_resolution_fill_resizes_header_and_time_gutter_with_lanczos(
    tmp_path: Path,
) -> None:
    logical = tmp_path / "logical.png"
    native = tmp_path / "header-gutter-grid.png"
    output = tmp_path / "output.png"
    Image.new("RGB", (126, 72), "#7986CB").save(logical)
    Image.new("RGB", (1841, 852), "#202124").save(native)

    report = compose_output_mode(
        logical,
        native,
        output,
        HybridOutputMode.HEADER_PRESERVED_FILL,
        HIGH_RESOLUTION,
        native_header_height=75,
        native_time_gutter_width=73,
    )

    assert report["header_resample_method"] == "lanczos"
    assert report["time_gutter_resample_method"] == "lanczos"
    assert report["grid_resample_method"] == "nearest-neighbor"
    assert report["header_source_rect"] == [0, 0, 1841, 75]
    assert report["time_gutter_source_rect"] == [0, 75, 73, 777]
    assert report["grid_source_rect"] == [73, 75, 1768, 777]
    assert report["header_output_rect"] == [0, 0, 1512, 76]
    assert report["time_gutter_output_rect"] == [0, 76, 60, 788]
    assert report["grid_output_rect"] == [60, 76, 1452, 788]


def test_final_frame_validation_uses_selected_high_resolution(tmp_path: Path) -> None:
    for index in range(108):
        Image.new("RGB", HIGH_RESOLUTION, "#7986CB").save(tmp_path / f"frame_{index:03d}.png")

    paths = validate_final_frames(tmp_path, HIGH_RESOLUTION)

    assert len(paths) == 108


def test_legacy_capture_error_sanity_is_stale_for_current_implementation(
    tmp_path: Path,
) -> None:
    plan = _hybrid_plan(tmp_path)
    store = HybridCaptureStore(tmp_path / "runs")
    old = store.sanity_directory("hybrid-test") / "sanity-report.json"
    old.parent.mkdir(parents=True)
    old.write_text(
        '{"schema_version":"1.0","automated_result":"CAPTURE ERROR"}',
        encoding="utf-8",
    )

    status, version = final_sanity_gate_status(
        store,
        "hybrid-test",
        HybridOutputMode.HEADER_PRESERVED_FILL,
        HIGH_RESOLUTION,
    )

    assert status == "STALE LEGACY REPORT - RERUN REQUIRED"
    assert version == "legacy-schema-1.0"
    service = HybridCaptureService(store, lambda _p, _z: ReadOnlyFakeGateway())
    with pytest.raises(CalendarAnimError, match="Current final sanity"):
        service.validate_final_capture_gate(
            plan, HybridOutputMode.HEADER_PRESERVED_FILL, HIGH_RESOLUTION
        )


def test_current_six_frame_sanity_pass_allows_matching_full_capture_gate(
    tmp_path: Path,
) -> None:
    plan = _hybrid_plan(tmp_path)
    store = HybridCaptureStore(tmp_path / "runs")
    gateway = ReadOnlyFakeGateway()
    service = HybridCaptureService(store, lambda _p, _z: gateway)

    report = service.capture_final_sanity(
        plan,
        [24, 40, 60, 80, 100, 108],
        "account-b",
        HybridOutputMode.HEADER_PRESERVED_FILL,
        HIGH_RESOLUTION,
    )

    assert report.automated_result == "PASS"
    assert report.capture_implementation_version == CURRENT_CAPTURE_IMPLEMENTATION_VERSION
    assert report.dom_event_count_is_gate is False
    assert all(item.passed for item in report.results)
    assert final_sanity_allows_capture(
        report, HybridOutputMode.HEADER_PRESERVED_FILL, HIGH_RESOLUTION
    )
    accepted = service.validate_final_capture_gate(
        plan, HybridOutputMode.HEADER_PRESERVED_FILL, HIGH_RESOLUTION
    )
    assert accepted.automated_result == "PASS"
    status, version = final_sanity_gate_status(
        store,
        plan.run_id,
        HybridOutputMode.HEADER_PRESERVED_FILL,
        HIGH_RESOLUTION,
    )
    assert status == "PASS"
    assert version == CURRENT_CAPTURE_IMPLEMENTATION_VERSION
    directory = store.final_sanity_directory(
        plan.run_id, HybridOutputMode.HEADER_PRESERVED_FILL, HIGH_RESOLUTION
    )
    assert (directory / "sanity-contact-sheet.png").is_file()
    assert all((directory / f"frame-{frame:03d}.png").is_file() for frame in report.frames_checked)


def _prime_navigation_gates(
    store: HybridCaptureStore,
    plan: HybridCapturePlan,
    mode: HybridOutputMode = HybridOutputMode.HEADER_PRESERVED_FILL,
    resolution: tuple[int, int] = HIGH_RESOLUTION,
) -> None:
    sanity_frames = [24, 40, 60, 80, 100, 108]
    for human_frame in sanity_frames:
        frame = plan.frames[human_frame - 1]
        _write_uniform_frame_plan(Path(frame.source_frame_plan), frame.frame_index)
    HybridCaptureService(store, lambda _p, zoom: ReadOnlyFakeGateway(zoom)).capture_final_sanity(
        plan, sanity_frames, "account-b", mode, resolution
    )
    store.save_json_report(
        store.profile_preflight_report_path(plan.run_id),
        {
            "schema_version": "1.0",
            "profile_navigation_version": CURRENT_PROFILE_NAVIGATION_VERSION,
            "run_id": plan.run_id,
            "result": "PASS",
            "profiles": [
                {"profile": "account-a", "status": "PASS"},
                {"profile": "account-b", "status": "PASS"},
            ],
            "google_calendar_writes": False,
        },
    )


def test_profile_preflight_launches_a_then_b_with_locked_zooms(tmp_path: Path) -> None:
    plan = _hybrid_plan(tmp_path)
    store = HybridCaptureStore(tmp_path / "runs")
    launched: list[tuple[str, int]] = []

    def factory(profile: str, zoom: int) -> ReadOnlyFakeGateway:
        launched.append((profile, zoom))
        return ReadOnlyFakeGateway(float(zoom))

    report = HybridCaptureService(store, factory).check_final_capture_profiles(plan)

    assert report["result"] == "PASS"
    assert launched == [("account-a", 33), ("account-b", 90)]
    assert store.profile_preflight_report_path(plan.run_id).is_file()


def test_gateway_factory_keeps_persistent_profile_directories_isolated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    profiles = {
        "account-a": SimpleNamespace(
            profile_name="account-a",
            browser_profile_directory=tmp_path / "browser-a",
            authenticated_google_account="a@example.com",
            calendar_name="Calendar Animation Lab",
            capture_zoom_percent=33,
        ),
        "account-b": SimpleNamespace(
            profile_name="account-b",
            browser_profile_directory=tmp_path / "browser-b",
            authenticated_google_account="b@example.com",
            calendar_name="Calendar Animation Lab B",
            capture_zoom_percent=90,
        ),
    }
    monkeypatch.setattr(
        hybrid_commands,
        "CalendarProfileStore",
        lambda: SimpleNamespace(load=lambda name: profiles[name]),
    )

    factory = hybrid_commands._gateway_factory(0, 30)
    gateway_a = factory("account-a", 33)
    gateway_b = factory("account-b", 90)
    assert isinstance(gateway_a, hybrid_commands.PlaywrightCalendarCaptureGateway)
    assert isinstance(gateway_b, hybrid_commands.PlaywrightCalendarCaptureGateway)
    config_a = gateway_a.config
    config_b = gateway_b.config

    assert config_a.profile_directory == tmp_path / "browser-a"
    assert config_b.profile_directory == tmp_path / "browser-b"
    assert config_a.profile_directory != config_b.profile_directory
    assert config_a.expected_google_account == "a@example.com"
    assert config_b.expected_google_account == "b@example.com"


def test_transition_closes_a_before_opening_b_and_uses_final_composition(
    tmp_path: Path,
) -> None:
    plan = _hybrid_plan(tmp_path)
    store = HybridCaptureStore(tmp_path / "runs")
    _prime_navigation_gates(store, plan)
    lifecycle: list[str] = []

    class TrackingGateway(ReadOnlyFakeGateway):
        def __init__(self, profile: str, zoom: int) -> None:
            super().__init__(float(zoom))
            self.profile = profile

        def __enter__(self) -> "TrackingGateway":
            lifecycle.append(f"enter:{self.profile}")
            return self

        def __exit__(
            self,
            exc_type: type[BaseException] | None,
            exc_value: BaseException | None,
            traceback: TracebackType | None,
        ) -> None:
            lifecycle.append(f"exit:{self.profile}")

    service = HybridCaptureService(store, lambda profile, zoom: TrackingGateway(profile, zoom))
    report = service.capture_profile_transition(
        plan, HybridOutputMode.HEADER_PRESERVED_FILL, HIGH_RESOLUTION
    )

    assert report["result"] == "PASS"
    assert lifecycle == [
        "enter:account-a",
        "exit:account-a",
        "enter:account-b",
        "exit:account-b",
    ]
    directory = store.profile_transition_directory(plan.run_id)
    for name in ("frame_022-account-a.png", "frame_023-account-b.png"):
        with Image.open(directory / name) as image:
            assert image.size == HIGH_RESOLUTION


def test_transition_checkpoint_preserves_a_when_b_fails_then_resumes_b_only(
    tmp_path: Path,
) -> None:
    plan = _hybrid_plan(tmp_path)
    store = HybridCaptureStore(tmp_path / "runs")
    _prime_navigation_gates(store, plan)
    launched: list[str] = []

    class FailingBGateway(ReadOnlyFakeGateway):
        def wait_until_ready(self, week_start: date, minimum_event_count: int) -> None:
            raise CalendarAnimError("account-b transition failure")

        def reload_current_week(self, week_start: date, minimum_event_count: int) -> None:
            raise CalendarAnimError("account-b transition failure")

    def failing_factory(profile: str, zoom: int) -> ReadOnlyFakeGateway:
        launched.append(profile)
        return FailingBGateway(zoom) if profile == "account-b" else ReadOnlyFakeGateway(zoom)

    service = HybridCaptureService(store, failing_factory)
    with pytest.raises(CalendarAnimError, match="CAPTURE LOAD FAILURE"):
        service.capture_profile_transition(
            plan, HybridOutputMode.HEADER_PRESERVED_FILL, HIGH_RESOLUTION
        )

    partial = store.load_json_report(store.profile_transition_report_path(plan.run_id))
    partial_frames = partial["frames"]
    assert isinstance(partial_frames, list)
    assert partial_frames[0]["status"] == "COMPLETED"  # type: ignore[index]
    assert partial_frames[1]["status"] == "FAILED"  # type: ignore[index]
    launched.clear()

    resumed = HybridCaptureService(
        store, lambda profile, zoom: launched.append(profile) or ReadOnlyFakeGateway(zoom)
    ).capture_profile_transition(plan, HybridOutputMode.HEADER_PRESERVED_FILL, HIGH_RESOLUTION)

    assert resumed["result"] == "PASS"
    assert launched == ["account-b"]


def test_full_resume_skips_completed_account_a_profile_context(tmp_path: Path) -> None:
    plan = _hybrid_plan(tmp_path)
    store = HybridCaptureStore(tmp_path / "runs")
    _prime_navigation_gates(store, plan)
    transition_directory = store.profile_transition_directory(plan.run_id)
    transition_directory.mkdir(parents=True, exist_ok=True)
    for frame_index, profile in ((22, "account-a"), (23, "account-b")):
        Image.new("RGB", HIGH_RESOLUTION, "#7986CB").save(
            transition_directory / f"frame_{frame_index:03d}-{profile}.png"
        )
    store.save_json_report(
        store.profile_transition_report_path(plan.run_id),
        {
            "schema_version": "1.0",
            "profile_navigation_version": CURRENT_PROFILE_NAVIGATION_VERSION,
            "run_id": plan.run_id,
            "output_mode": HybridOutputMode.HEADER_PRESERVED_FILL.value,
            "output_resolution": list(HIGH_RESOLUTION),
            "result": "PASS",
            "frames": [],
            "google_calendar_writes": False,
        },
    )
    state = store.initialize_state(plan, HybridOutputMode.HEADER_PRESERVED_FILL, HIGH_RESOLUTION)
    for frame in plan.frames[:23]:
        state.frame(frame.frame_index).status = HybridFrameStatus.COMPLETED
        output = store.final_frame_path(
            plan.run_id,
            frame.frame_index,
            HybridOutputMode.HEADER_PRESERVED_FILL,
            HIGH_RESOLUTION,
        )
        output.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", HIGH_RESOLUTION, "#7986CB").save(output)
    store.save_state(state)
    launched: list[str] = []

    class StopAtBGateway(ReadOnlyFakeGateway):
        def wait_until_ready(self, week_start: date, minimum_event_count: int) -> None:
            error = CalendarAnimError("stop after proving account-a was skipped")
            error.non_retryable = True  # type: ignore[attr-defined]
            raise error

    def factory(profile: str, zoom: int) -> ReadOnlyFakeGateway:
        launched.append(profile)
        return StopAtBGateway(zoom)

    with pytest.raises(CalendarAnimError, match="CAPTURE LOAD FAILURE"):
        HybridCaptureService(store, factory).capture_final(
            plan, state, HybridOutputMode.HEADER_PRESERVED_FILL, HIGH_RESOLUTION
        )

    assert launched == ["account-b"]
    assert all(state.frame(index).status is HybridFrameStatus.COMPLETED for index in range(23))
    failure = (
        store.final_capture_failure_directory(
            plan.run_id, HybridOutputMode.HEADER_PRESERVED_FILL, HIGH_RESOLUTION
        )
        / "capture-failure-account-b-frame-024-attempt-1.json"
    )
    payload = store.load_json_report(failure)
    assert payload["profile"] == "account-b"
    assert payload["human_frame"] == 24
    assert payload["frame_index"] == 23
    assert payload["week_start"] == "2028-01-02"
    assert payload["zoom_expected"] == 90


def test_single_profile_preview_uses_final_capture_code_and_never_mutates_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _hybrid_plan(tmp_path)
    source.capture_strategy = "single-profile-account-b"
    for frame in source.frames:
        frame.calendar_profile = "account-b"
        frame.calendar_name = "Calendar Animation Lab B"
        frame.capture_zoom_percent = 90
    source = HybridCapturePlan.model_validate(source.model_dump())
    store = AccountBSingleCaptureStore(tmp_path / "runs")
    mode = HybridOutputMode.HEADER_PRESERVED_FILL
    resolution = HIGH_RESOLUTION
    state = store.initialize_state(source, mode, resolution)
    state_before = store.state_path(source.run_id, mode, resolution).read_bytes()
    calls: list[tuple[int, Path]] = []
    minimum_counts: list[int] = []
    original = HybridCaptureService._capture_composed_frame

    def tracked(
        self,
        gateway,
        frame,
        raw,
        logical,
        header,
        output,
        selected_mode,
        selected_resolution,
        debug,
        *,
        minimum_event_count=0,
    ):  # type: ignore[no-untyped-def]
        calls.append((frame.frame_index, output))
        minimum_counts.append(minimum_event_count)
        return original(
            self,
            gateway,
            frame,
            raw,
            logical,
            header,
            output,
            selected_mode,
            selected_resolution,
            debug,
            minimum_event_count=minimum_event_count,
        )

    monkeypatch.setattr(HybridCaptureService, "_capture_composed_frame", tracked)
    launched: list[tuple[str, int]] = []

    def factory(profile: str, zoom: int) -> ReadOnlyFakeGateway:
        launched.append((profile, zoom))
        return ReadOnlyFakeGateway(zoom)

    report = HybridCaptureService(store, factory).capture_final_single_profile_preview(
        source, [23, 24], mode, resolution
    )

    assert calls == [
        (22, store.preview_frame_path(source.run_id, 22)),
        (23, store.preview_frame_path(source.run_id, 23)),
    ]
    assert launched == [("account-b", 90)]
    assert report.frames[0].human_frame == 23
    assert report.frames[0].frame_index == 22
    assert report.frames[1].human_frame == 24
    assert report.frames[1].frame_index == 23
    assert all(item.left_time_gutter_present for item in report.frames)
    assert all(item.timezone_label_present for item in report.frames)
    assert all(item.create_button_excluded for item in report.frames)
    assert report.geometry_consistent is True
    assert store.state_path(source.run_id, mode, resolution).read_bytes() == state_before
    assert all(item.status is HybridFrameStatus.PENDING for item in state.frames)
    assert not store.final_frame_path(source.run_id, 22, mode, resolution).exists()

    launched.clear()
    calls.clear()
    HybridCaptureService(store, factory).capture_final_single_profile_preview(
        source,
        [23, 24],
        mode,
        resolution,
        fresh_session_per_frame=True,
    )
    assert calls == [
        (22, store.preview_frame_path(source.run_id, 22)),
        (23, store.preview_frame_path(source.run_id, 23)),
    ]
    assert launched == [("account-b", 90), ("account-b", 90)]
    assert minimum_counts[-2:] == [0, 0]

    launched.clear()
    minimum_counts.clear()
    HybridCaptureService(store, factory).capture_final_single_profile_preview(
        source,
        [23, 24],
        mode,
        resolution,
        fresh_session_per_frame=True,
        minimum_event_count=1,
    )
    assert launched == [("account-b", 90), ("account-b", 90)]
    assert minimum_counts == [1, 1]


def test_single_profile_full_capture_forwards_visual_gate_and_progress_callback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan = _hybrid_plan(tmp_path)
    plan.capture_strategy = "single-profile-account-b"
    for frame in plan.frames:
        frame.calendar_profile = "account-b"
        frame.calendar_name = "Calendar Animation Lab B"
        frame.capture_zoom_percent = 90
    plan = HybridCapturePlan.model_validate(plan.model_dump())
    store = AccountBSingleCaptureStore(tmp_path / "runs")
    state = store.initialize_state(plan, HybridOutputMode.HEADER_PRESERVED_FILL, HIGH_RESOLUTION)
    received: dict[str, object] = {}

    def callback(*_: object) -> None:
        return None

    def retry_callback(*_: object) -> None:
        return None

    def capture_profiles(
        self,
        selected_plan,
        selected_state,
        mode,
        resolution,
        profiles,
        *,
        minimum_event_count=0,
        fresh_session_per_frame=False,
        fresh_session_attempts=1,
        progress_callback=None,
        session_retry_callback=None,
    ):  # type: ignore[no-untyped-def]
        received.update(
            {
                "profiles": profiles,
                "minimum_event_count": minimum_event_count,
                "fresh_session_per_frame": fresh_session_per_frame,
                "fresh_session_attempts": fresh_session_attempts,
                "progress_callback": progress_callback,
                "session_retry_callback": session_retry_callback,
            }
        )

    monkeypatch.setattr(HybridCaptureService, "_capture_final_profiles", capture_profiles)
    monkeypatch.setattr(HybridCaptureService, "_validate_final_sequence", lambda *args: None)

    HybridCaptureService(store, lambda *_: None).capture_final_single_profile(
        plan,
        state,
        HybridOutputMode.HEADER_PRESERVED_FILL,
        HIGH_RESOLUTION,
        minimum_event_count=1,
        fresh_session_per_frame=True,
        fresh_session_attempts=3,
        progress_callback=callback,
        session_retry_callback=retry_callback,
    )

    assert received == {
        "profiles": (("account-b", 90),),
        "minimum_event_count": 1,
        "fresh_session_per_frame": True,
        "fresh_session_attempts": 3,
        "progress_callback": callback,
        "session_retry_callback": retry_callback,
    }


def test_full_capture_can_recycle_browser_session_after_every_frame(tmp_path: Path) -> None:
    plan = _hybrid_plan(tmp_path)
    plan.capture_strategy = "single-profile-account-b"
    for frame in plan.frames:
        frame.calendar_profile = "account-b"
        frame.calendar_name = "Calendar Animation Lab B"
        frame.capture_zoom_percent = 90
    plan = HybridCapturePlan.model_validate(plan.model_dump())
    store = AccountBSingleCaptureStore(tmp_path / "runs")
    mode = HybridOutputMode.HEADER_PRESERVED_FILL
    state = store.initialize_state(plan, mode, HIGH_RESOLUTION)
    for item in state.frames[2:]:
        item.status = HybridFrameStatus.COMPLETED
        output = store.final_frame_path(plan.run_id, item.frame_index, mode, HIGH_RESOLUTION)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.touch()
    launched: list[ReadOnlyFakeGateway] = []

    def factory(profile: str, zoom: int) -> ReadOnlyFakeGateway:
        assert (profile, zoom) == ("account-b", 90)
        gateway = ReadOnlyFakeGateway(zoom)
        launched.append(gateway)
        return gateway

    HybridCaptureService(store, factory)._capture_final_profiles(
        plan,
        state,
        mode,
        HIGH_RESOLUTION,
        (("account-b", 90),),
        minimum_event_count=1,
        fresh_session_per_frame=True,
    )

    assert len(launched) == 2
    assert launched[0].opened == [plan.frames[0].week_start]
    assert launched[1].opened == [plan.frames[1].week_start]
    assert state.frames[0].status is HybridFrameStatus.COMPLETED
    assert state.frames[1].status is HybridFrameStatus.COMPLETED


def test_full_capture_retries_failed_frame_in_a_new_browser_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan = _hybrid_plan(tmp_path)
    plan.capture_strategy = "single-profile-account-b"
    for frame in plan.frames:
        frame.calendar_profile = "account-b"
        frame.calendar_name = "Calendar Animation Lab B"
        frame.capture_zoom_percent = 90
    plan = HybridCapturePlan.model_validate(plan.model_dump())
    store = AccountBSingleCaptureStore(tmp_path / "runs")
    mode = HybridOutputMode.HEADER_PRESERVED_FILL
    state = store.initialize_state(plan, mode, HIGH_RESOLUTION)
    for item in state.frames[1:]:
        item.status = HybridFrameStatus.COMPLETED
        output = store.final_frame_path(plan.run_id, item.frame_index, mode, HIGH_RESOLUTION)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.touch()
    launched: list[ReadOnlyFakeGateway] = []
    captures = 0
    retries: list[tuple[int, int, int]] = []

    def factory(profile: str, zoom: int) -> ReadOnlyFakeGateway:
        assert (profile, zoom) == ("account-b", 90)
        gateway = ReadOnlyFakeGateway(zoom)
        launched.append(gateway)
        return gateway

    service = HybridCaptureService(store, factory)

    def capture_frame(
        gateway,
        frame,
        raw,
        logical,
        header,
        output,
        selected_mode,
        resolution,
        debug_directory,
        *,
        minimum_event_count=0,
    ):  # type: ignore[no-untyped-def]
        nonlocal captures
        captures += 1
        if captures == 1:
            raise CalendarAnimError("transient empty Calendar grid")
        output.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", resolution, "black").save(output)
        return {}

    monkeypatch.setattr(service, "_capture_composed_frame", capture_frame)

    service._capture_final_profiles(
        plan,
        state,
        mode,
        HIGH_RESOLUTION,
        (("account-b", 90),),
        fresh_session_per_frame=True,
        fresh_session_attempts=3,
        session_retry_callback=lambda frame, attempt, total, error: retries.append(
            (frame.frame_index, attempt, total)
        ),
    )

    assert captures == 2
    assert len(launched) == 2
    assert retries == [(0, 1, 3)]
    assert state.frames[0].status is HybridFrameStatus.COMPLETED
