from datetime import date, datetime
from pathlib import Path
from types import TracebackType

from PIL import Image

from calendar_anim.calendar.capture.final_media import (
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
from calendar_anim.calendar.hybrid_capture.artifacts import (
    HybridCaptureStore,
    normalize_grid,
)
from calendar_anim.calendar.hybrid_capture.media import build_final_visual_command
from calendar_anim.calendar.hybrid_capture.models import (
    HybridCapturePlan,
    HybridFramePlan,
    HybridSanityReport,
    SanityFrameResult,
)
from calendar_anim.calendar.hybrid_capture.service import HybridCaptureService
from calendar_anim.calendar.subcolumn_ordering import SubcolumnOrderStrategy


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
    def __init__(self) -> None:
        self.opened: list[date] = []
        self.waited: list[tuple[date, int]] = []

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
    assert gateway.waited == [(date(2028, 1, 2), 1)]
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
