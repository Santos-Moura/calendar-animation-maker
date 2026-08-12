from datetime import date, datetime
from pathlib import Path
from types import TracebackType

import pytest
from PIL import Image

from calendar_anim.browser.playwright_gateway import (
    CaptureReadinessError,
    EventPopulationAudit,
    deduplicate_event_records,
    logical_grid_clip,
    structural_grid_bounds_from_diagnostics,
    wait_for_stable_population,
    wait_for_stable_visual_grid,
)
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
        self.population_waits: list[int] = []
        self.reloads: list[date] = []

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
