import os
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from PIL import Image, ImageDraw

from calendar_anim.calendar.frame_mapping.models import SingleFrameCalendarPlan
from calendar_anim.calendar.hybrid_capture.models import (
    FinalHybridSanityReport,
    HybridCapturePlan,
    HybridCaptureState,
    HybridFrameState,
    HybridOutputMode,
    HybridSanityReport,
    HybridSeamReport,
)
from calendar_anim.exceptions import CalendarAnimError

LEGACY_RESOLUTION = (504, 288)
HIGH_RESOLUTION = (1512, 864)


def parse_output_resolution(value: str) -> tuple[int, int]:
    try:
        width_text, height_text = value.lower().split("x", maxsplit=1)
        width, height = int(width_text), int(height_text)
    except ValueError as error:
        raise CalendarAnimError(
            "--resolution must use WIDTHxHEIGHT, for example 1512x864"
        ) from error
    if width <= 0 or height <= 0 or width > 7680 or height > 4320:
        raise CalendarAnimError("Output resolution is outside the supported positive range")
    if width * 4 != height * 7:
        raise CalendarAnimError("Hybrid output resolution must preserve the 7:4 aspect ratio")
    if width % 2 or height % 2:
        raise CalendarAnimError("Hybrid output dimensions must be even for yuv420p")
    return width, height


def resolution_name(resolution: tuple[int, int]) -> str:
    return f"{resolution[0]}x{resolution[1]}"


def write_atomic(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()
    return path


class HybridCaptureStore:
    def __init__(self, root: Path = Path("output/hybrid-runs")) -> None:
        self.root = root

    def run_directory(self, run_id: str) -> Path:
        return self.root / run_id

    def plan_path(self, run_id: str) -> Path:
        return self.run_directory(run_id) / "hybrid-capture-plan.json"

    def state_path(
        self,
        run_id: str,
        mode: HybridOutputMode = HybridOutputMode.PIXEL_FAITHFUL,
        resolution: tuple[int, int] = LEGACY_RESOLUTION,
    ) -> Path:
        name = f"{mode.value}-{resolution_name(resolution)}.json"
        return self.run_directory(run_id) / "final-capture-state" / name

    def sanity_directory(self, run_id: str) -> Path:
        return self.run_directory(run_id) / "sanity"

    def final_sanity_directory(
        self,
        run_id: str,
        mode: HybridOutputMode,
        resolution: tuple[int, int],
    ) -> Path:
        return (
            self.run_directory(run_id)
            / "sanity-hires"
            / mode.directory_name
            / resolution_name(resolution)
        )

    def final_sanity_report_path(
        self,
        run_id: str,
        mode: HybridOutputMode,
        resolution: tuple[int, int],
    ) -> Path:
        return self.final_sanity_directory(run_id, mode, resolution) / "sanity-report.json"

    def sanity_frame_directory(self, run_id: str, human_frame: int) -> Path:
        return self.sanity_directory(run_id) / f"frame-{human_frame:03d}"

    def debug_frame_directory(self, run_id: str, human_frame: int) -> Path:
        return self.run_directory(run_id) / "capture-debug" / f"frame-{human_frame:03d}"

    def high_resolution_debug_directory(self, run_id: str, human_frame: int) -> Path:
        return self.run_directory(run_id) / "capture-debug" / f"frame-{human_frame:03d}-hires"

    def archive_sanity(self, run_id: str) -> Path | None:
        """Move an earlier sanity run aside before a new read-only capture."""

        source = self.sanity_directory(run_id)
        if not source.exists() or not any(source.iterdir()):
            return None
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
        destination = self.run_directory(run_id) / "sanity-backups" / stamp
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source), str(destination))
        return destination

    def final_frames_directory(
        self,
        run_id: str,
        mode: HybridOutputMode = HybridOutputMode.PIXEL_FAITHFUL,
        resolution: tuple[int, int] = LEGACY_RESOLUTION,
    ) -> Path:
        return (
            self.run_directory(run_id)
            / "final-frames"
            / mode.directory_name
            / resolution_name(resolution)
        )

    def final_frame_path(
        self,
        run_id: str,
        frame_index: int,
        mode: HybridOutputMode = HybridOutputMode.PIXEL_FAITHFUL,
        resolution: tuple[int, int] = LEGACY_RESOLUTION,
    ) -> Path:
        return (
            self.final_frames_directory(run_id, mode, resolution) / f"frame_{frame_index:03d}.png"
        )

    def final_raw_path(
        self,
        run_id: str,
        frame_index: int,
        mode: HybridOutputMode,
        resolution: tuple[int, int] = LEGACY_RESOLUTION,
    ) -> Path:
        return (
            self.run_directory(run_id)
            / "final-capture"
            / mode.directory_name
            / resolution_name(resolution)
            / "raw"
            / f"frame_{frame_index:03d}.png"
        )

    def final_logical_path(
        self,
        run_id: str,
        frame_index: int,
        mode: HybridOutputMode,
        resolution: tuple[int, int] = LEGACY_RESOLUTION,
    ) -> Path:
        return (
            self.run_directory(run_id)
            / "final-capture"
            / mode.directory_name
            / resolution_name(resolution)
            / "logical"
            / f"frame_{frame_index:03d}.png"
        )

    def final_header_path(
        self,
        run_id: str,
        frame_index: int,
        mode: HybridOutputMode,
        resolution: tuple[int, int] = LEGACY_RESOLUTION,
    ) -> Path:
        return (
            self.run_directory(run_id)
            / "final-capture"
            / mode.directory_name
            / resolution_name(resolution)
            / "header-grid"
            / f"frame_{frame_index:03d}.png"
        )

    def final_directory(
        self, run_id: str, mode: HybridOutputMode, resolution: tuple[int, int]
    ) -> Path:
        return (
            self.run_directory(run_id) / "final" / mode.directory_name / resolution_name(resolution)
        )

    def save_plan(self, plan: HybridCapturePlan) -> Path:
        path = self.plan_path(plan.run_id)
        serialized = plan.model_dump_json(indent=2) + "\n"
        if path.exists() and path.read_text(encoding="utf-8") != serialized:
            raise CalendarAnimError("Existing hybrid capture plan differs from locked plan")
        return write_atomic(path, serialized)

    def load_plan(self, run_id: str) -> HybridCapturePlan:
        try:
            return HybridCapturePlan.model_validate_json(
                self.plan_path(run_id).read_text(encoding="utf-8")
            )
        except (OSError, ValueError) as error:
            raise CalendarAnimError("Invalid or missing hybrid capture plan") from error

    def initialize_state(
        self,
        plan: HybridCapturePlan,
        mode: HybridOutputMode = HybridOutputMode.PIXEL_FAITHFUL,
        resolution: tuple[int, int] = LEGACY_RESOLUTION,
    ) -> HybridCaptureState:
        path = self.state_path(plan.run_id, mode, resolution)
        if path.exists():
            state = HybridCaptureState.model_validate_json(path.read_text(encoding="utf-8"))
            if state.output_mode is not mode:
                raise CalendarAnimError("Hybrid capture state output mode differs")
            if (state.output_width, state.output_height) != resolution:
                raise CalendarAnimError("Hybrid capture state resolution differs")
            expected = [(item.frame_index, item.calendar_profile) for item in plan.frames]
            actual = [(item.frame_index, item.profile) for item in state.frames]
            if actual != expected:
                raise CalendarAnimError("Hybrid capture state differs from locked frame boundary")
            return state
        state = HybridCaptureState(
            run_id=plan.run_id,
            output_mode=mode,
            output_width=resolution[0],
            output_height=resolution[1],
            frames=[
                HybridFrameState(frame_index=item.frame_index, profile=item.calendar_profile)
                for item in plan.frames
            ],
            updated_at=datetime.now(UTC),
        )
        self.save_state(state)
        return state

    def save_state(self, state: HybridCaptureState) -> Path:
        state.updated_at = datetime.now(UTC)
        return write_atomic(
            self.state_path(
                state.run_id,
                state.output_mode,
                (state.output_width, state.output_height),
            ),
            state.model_dump_json(indent=2) + "\n",
        )

    def save_sanity_report(self, report: HybridSanityReport) -> tuple[Path, Path]:
        directory = self.sanity_directory(report.run_id)
        json_path = write_atomic(
            directory / "sanity-report.json", report.model_dump_json(indent=2) + "\n"
        )
        text_path = write_atomic(directory / "sanity-report.txt", sanity_text(report))
        return json_path, text_path

    def load_sanity_report(self, run_id: str) -> HybridSanityReport:
        path = self.sanity_directory(run_id) / "sanity-report.json"
        try:
            return HybridSanityReport.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise CalendarAnimError(
                "Sanity capture must be completed before full capture"
            ) from error

    def save_final_sanity_report(self, report: FinalHybridSanityReport) -> Path:
        resolution = (report.output_width, report.output_height)
        return write_atomic(
            self.final_sanity_report_path(report.run_id, report.output_mode, resolution),
            report.model_dump_json(indent=2) + "\n",
        )

    def load_final_sanity_report(
        self,
        run_id: str,
        mode: HybridOutputMode,
        resolution: tuple[int, int],
    ) -> FinalHybridSanityReport:
        try:
            return FinalHybridSanityReport.model_validate_json(
                self.final_sanity_report_path(run_id, mode, resolution).read_text(encoding="utf-8")
            )
        except (OSError, ValueError) as error:
            raise CalendarAnimError(
                "Current final sanity is missing or invalid; run capture-hybrid-sanity"
            ) from error

    def seam_directory(self, run_id: str) -> Path:
        return self.run_directory(run_id) / "seam"

    def save_seam_report(self, report: HybridSeamReport) -> Path:
        return write_atomic(
            self.seam_directory(report.run_id) / "a-b-transition-geometry.json",
            report.model_dump_json(indent=2) + "\n",
        )

    def load_seam_report(self, run_id: str) -> HybridSeamReport:
        try:
            return HybridSeamReport.model_validate_json(
                (self.seam_directory(run_id) / "a-b-transition-geometry.json").read_text(
                    encoding="utf-8"
                )
            )
        except (OSError, ValueError) as error:
            raise CalendarAnimError("A/B seam validation must pass before full capture") from error


def normalize_grid(source: Path, destination: Path) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        with Image.open(source) as image:
            image.convert("RGB").resize((504, 288), Image.Resampling.NEAREST).save(destination)
    except OSError as error:
        raise CalendarAnimError(f"Could not normalize Calendar grid: {source}") from error
    return destination


def compose_output_mode(
    logical_source: Path,
    header_source: Path | None,
    destination: Path,
    mode: HybridOutputMode,
    resolution: tuple[int, int] = LEGACY_RESOLUTION,
    *,
    native_header_height: int | None = None,
) -> dict[str, object]:
    """Compose directly from native browser crops into the requested resolution."""

    source_path = logical_source if mode is HybridOutputMode.PIXEL_FAITHFUL else header_source
    if source_path is None:
        raise CalendarAnimError(f"Mode {mode.value} requires a week-header capture")
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        with Image.open(source_path) as opened:
            source = opened.convert("RGB")
            source_width, source_height = source.size
            target_width, target_height = resolution
            if source_width <= 0 or source_height <= 0:
                raise CalendarAnimError("Calendar mode source has invalid dimensions")
            if mode is HybridOutputMode.HEADER_PRESERVED_LETTERBOX:
                scale = min(target_width / source_width, target_height / source_height)
                content_size = (
                    max(1, round(source_width * scale)),
                    max(1, round(source_height * scale)),
                )
                resized = source.resize(content_size, Image.Resampling.NEAREST)
                output = Image.new("RGB", resolution, "#202124")
                offset = (
                    (target_width - content_size[0]) // 2,
                    (target_height - content_size[1]) // 2,
                )
                output.paste(resized, offset)
                resized.close()
                letterbox = content_size != resolution
                stretch = False
                header_method = "nearest-neighbor"
                grid_method = "nearest-neighbor"
            elif mode is HybridOutputMode.HEADER_PRESERVED_FILL and resolution != LEGACY_RESOLUTION:
                if native_header_height is None or not 0 < native_header_height < source_height:
                    raise CalendarAnimError(
                        "High-resolution header fill requires native header geometry"
                    )
                target_header_height = max(
                    1, round(target_height * native_header_height / source_height)
                )
                target_grid_height = target_height - target_header_height
                native_header = source.crop((0, 0, source_width, native_header_height))
                native_grid = source.crop((0, native_header_height, source_width, source_height))
                resized_header = native_header.resize(
                    (target_width, target_header_height), Image.Resampling.LANCZOS
                )
                resized_grid = native_grid.resize(
                    (target_width, target_grid_height), Image.Resampling.NEAREST
                )
                output = Image.new("RGB", resolution)
                output.paste(resized_header, (0, 0))
                output.paste(resized_grid, (0, target_header_height))
                for image in (native_header, native_grid, resized_header, resized_grid):
                    image.close()
                content_size = resolution
                offset = (0, 0)
                letterbox = False
                stretch = abs(source_width / source_height - target_width / target_height) > 1e-6
                header_method = "lanczos"
                grid_method = "nearest-neighbor"
            else:
                content_size = resolution
                offset = (0, 0)
                output = source.resize(content_size, Image.Resampling.NEAREST)
                letterbox = False
                stretch = (
                    mode is HybridOutputMode.HEADER_PRESERVED_FILL
                    and abs(source_width / source_height - target_width / target_height) > 1e-6
                )
                header_method = "nearest-neighbor"
                grid_method = "nearest-neighbor"
            output.save(destination)
            output.close()
            source.close()
    except OSError as error:
        raise CalendarAnimError(f"Could not compose Calendar mode: {source_path}") from error
    return {
        "mode": mode.value,
        "source": str(source_path),
        "source_dimensions": [source_width, source_height],
        "content_dimensions": list(content_size),
        "content_offset": list(offset),
        "final_dimensions": list(resolution),
        "header_included": mode.includes_header,
        "vertical_interval": "06:00-00:00",
        "letterbox": letterbox,
        "stretch": stretch,
        "logical_grid_normalization": mode is HybridOutputMode.PIXEL_FAITHFUL,
        "source_of_resize": "native browser crop",
        "intermediate_504x288": False,
        "resize_passes": 1,
        "header_resample_method": header_method,
        "grid_resample_method": grid_method,
        "resampling": (header_method if header_method == grid_method else "hybrid"),
        "blur_or_sharpen": False,
        "notes": _mode_notes(mode),
    }


def compose_high_resolution_comparison(
    native_crop: Path, final_output: Path, destination: Path
) -> Path:
    """Stack native and final images at actual size without downscaling either."""

    try:
        with Image.open(native_crop) as native_opened, Image.open(final_output) as final_opened:
            native = native_opened.convert("RGB")
            final = final_opened.convert("RGB")
            label_height = 36
            canvas_width = max(native.width, final.width)
            canvas = Image.new(
                "RGB",
                (canvas_width, label_height * 2 + native.height + final.height),
                "#202124",
            )
            draw = ImageDraw.Draw(canvas)
            draw.text((8, 10), f"NATIVE CROP {native.width}x{native.height}", fill="white")
            native_x = (canvas_width - native.width) // 2
            canvas.paste(native, (native_x, label_height))
            final_y = label_height + native.height
            draw.text(
                (8, final_y + 10),
                f"FINAL OUTPUT {final.width}x{final.height}",
                fill="white",
            )
            final_x = (canvas_width - final.width) // 2
            canvas.paste(final, (final_x, final_y + label_height))
            destination.parent.mkdir(parents=True, exist_ok=True)
            canvas.save(destination)
            for image in (native, final, canvas):
                image.close()
    except OSError as error:
        raise CalendarAnimError("Could not compose high-resolution comparison") from error
    return destination


def compose_final_sanity_contact_sheet(report: FinalHybridSanityReport, destination: Path) -> Path:
    """Create a readable 2x3 overview while retaining full-resolution frame files."""

    columns = 2
    preview_size = (756, 432)
    label_height = 36
    rows = (len(report.results) + columns - 1) // columns
    canvas = Image.new(
        "RGB",
        (preview_size[0] * columns, (preview_size[1] + label_height) * rows),
        "#202124",
    )
    draw = ImageDraw.Draw(canvas)
    for index, result in enumerate(report.results):
        column = index % columns
        row = index // columns
        x = column * preview_size[0]
        y = row * (preview_size[1] + label_height)
        status = "PASS" if result.passed else "FAIL"
        draw.text((x + 8, y + 10), f"FRAME {result.human_frame} / {status}", fill="white")
        if Path(result.output_artifact).is_file():
            try:
                with Image.open(result.output_artifact) as opened:
                    preview = opened.convert("RGB").resize(preview_size, Image.Resampling.LANCZOS)
                    canvas.paste(preview, (x, y + label_height))
                    preview.close()
            except OSError as error:
                raise CalendarAnimError(
                    f"Could not read final sanity frame: {result.output_artifact}"
                ) from error
        else:
            draw.rectangle(
                (x, y + label_height, x + preview_size[0], y + label_height + preview_size[1]),
                fill="#3c4043",
            )
            draw.text(
                (x + 12, y + label_height + 12), result.error or "CAPTURE ERROR", fill="#f28b82"
            )
    destination.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(destination)
    canvas.close()
    return destination


def compose_mode_contact_sheet(
    artifacts: list[tuple[HybridOutputMode, Path]], output: Path
) -> Path:
    if len(artifacts) != 3:
        raise CalendarAnimError("Mode comparison requires exactly three artifacts")
    canvas = Image.new("RGB", (1512, 324), "#202124")
    draw = ImageDraw.Draw(canvas)
    for column, (mode, path) in enumerate(artifacts):
        x = column * 504
        draw.text((x + 8, 9), mode.value, fill="white")
        try:
            with Image.open(path) as image:
                if image.size != (504, 288):
                    raise CalendarAnimError(f"Mode artifact is not 504x288: {path}")
                canvas.paste(image.convert("RGB"), (x, 36))
        except OSError as error:
            raise CalendarAnimError(f"Unreadable mode artifact: {path}") from error
    output.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(output)
    canvas.close()
    return output


def _mode_notes(mode: HybridOutputMode) -> str:
    if mode is HybridOutputMode.PIXEL_FAITHFUL:
        return (
            "Events-only structural crop normalized by the locked 126x72 logical grid; "
            "no header, gutter, visual fill stretch, blur, or sharpening."
        )
    if mode is HybridOutputMode.HEADER_PRESERVED_LETTERBOX:
        return "Header and 06:00-00:00 retained with aspect-preserving nearest-neighbor scaling."
    return "Header and 06:00-00:00 retained; non-uniform scaling is allowed to fill the frame."


def render_expected_frame(plan: SingleFrameCalendarPlan, destination: Path) -> Path:
    colors = {(cell.logical_x, cell.logical_y): cell.color_hex for cell in plan.mapped_cells}
    if len(colors) != 126 * 72:
        raise CalendarAnimError(f"Frame {plan.frame_index} is not a complete 126x72 logical grid")
    image = Image.new("RGB", (126, 72))
    for y in range(72):
        for x in range(126):
            image.putpixel((x, y), _hex_rgb(colors[(x, y)]))
    destination.parent.mkdir(parents=True, exist_ok=True)
    resized = image.resize((504, 288), Image.Resampling.NEAREST)
    resized.save(destination)
    image.close()
    resized.close()
    return destination


def compare_logical_cells(expected_path: Path, captured_path: Path) -> tuple[float, dict[str, int]]:
    with Image.open(expected_path) as expected_source, Image.open(captured_path) as captured_source:
        expected = expected_source.convert("RGB").resize((126, 72), Image.Resampling.NEAREST)
        captured = captured_source.convert("RGB").resize((126, 72), Image.Resampling.NEAREST)
        expected_pixels = cast(list[tuple[int, int, int]], list(expected.get_flattened_data()))
        captured_pixels = cast(list[tuple[int, int, int]], list(captured.get_flattened_data()))
        palette = sorted(set(expected_pixels))
        matches = 0
        classified: dict[str, int] = {}
        for expected_pixel, actual_pixel in zip(expected_pixels, captured_pixels, strict=True):
            nearest = min(palette, key=lambda color: _distance(color, actual_pixel))
            key = _rgb_css(nearest)
            classified[key] = classified.get(key, 0) + 1
            matches += nearest == expected_pixel
        expected.close()
        captured.close()
    return matches / (126 * 72), classified


def expected_distribution(plan: SingleFrameCalendarPlan) -> dict[str, int]:
    result: dict[str, int] = {}
    for cell in plan.mapped_cells:
        key = cell.color_hex.upper()
        result[key] = result.get(key, 0) + 1
    return dict(sorted(result.items()))


def compose_sanity_contact_sheet(report: HybridSanityReport, output: Path) -> Path:
    row_height = 324
    label_width = 110
    image = Image.new("RGB", (label_width + 1008, row_height * len(report.results) + 36), "#202124")
    draw = ImageDraw.Draw(image)
    draw.text((label_width, 10), "EXPECTED / LOCAL", fill="white")
    draw.text((label_width + 504, 10), "CALENDAR CAPTURE", fill="white")
    for row, result in enumerate(report.results):
        y = 36 + row * row_height
        draw.text((10, y + 8), f"FRAME {result.human_frame}", fill="white")
        draw.text((10, y + 26), f"match {result.logical_cell_match_ratio:.1%}", fill="white")
        for x, path in (
            (label_width, result.expected_artifact),
            (label_width + 504, result.normalized_artifact),
        ):
            if Path(path).is_file():
                with Image.open(path) as source:
                    image.paste(source.convert("RGB"), (x, y))
            else:
                draw.rectangle((x, y, x + 503, y + 287), fill="#3c4043")
                draw.text((x + 12, y + 12), "CAPTURE ERROR", fill="#f28b82")
    output.parent.mkdir(parents=True, exist_ok=True)
    image.save(output)
    image.close()
    return output


def compose_seam_geometry(left: Path, right: Path, output: Path) -> Path:
    with Image.open(left) as left_opened, Image.open(right) as right_opened:
        if left_opened.size != right_opened.size:
            raise CalendarAnimError("A/B seam inputs must have equal resolution")
        width, height = left_opened.size
        image = Image.new("RGB", (width * 2, height + 36), "#202124")
        draw = ImageDraw.Draw(image)
        draw.text((10, 8), "FRAME 23 / ACCOUNT A", fill="white")
        draw.text((width + 10, 8), "FRAME 24 / ACCOUNT B", fill="white")
        image.paste(left_opened.convert("RGB"), (0, 36))
        image.paste(right_opened.convert("RGB"), (width, 36))
    output.parent.mkdir(parents=True, exist_ok=True)
    image.save(output)
    image.close()
    return output


def sanity_text(report: HybridSanityReport) -> str:
    lines = [
        "HYBRID SANITY",
        "=============",
        "",
        f"Run: {report.run_id}",
        f"Profile: {report.profile}",
        f"Calendar: {report.calendar}",
        f"Frames checked: {', '.join(str(value) for value in report.frames_checked)}",
        "",
    ]
    for item in report.results:
        lines.extend(
            [
                f"Frame {item.human_frame} (index {item.frame_index}):",
                f"  Expected occurrences: {item.expected_occurrences}",
                f"  Rendered DOM events: {item.rendered_dom_events}",
                f"  Raw DOM nodes: {item.raw_dom_nodes}",
                f"  Unique event chips: {item.unique_event_chips}",
                f"  Capture success: {item.capture_success}",
                f"  Capture load success: {item.capture_load_success}",
                f"  Navigation complete: {item.navigation_complete}",
                f"  Capture retry cycles: {item.capture_retry_cycles}",
                f"  Capture error: {item.capture_error or 'none'}",
                f"  Normalized geometry: {item.normalized_width}x{item.normalized_height}",
                f"  Grid bounds: {item.grid_left:.3f}, {item.grid_top:.3f}, "
                f"{item.grid_right:.3f}, {item.grid_bottom:.3f}",
                f"  Logical cell match: {item.logical_cell_match_ratio:.3%}",
                f"  Obvious missing content: {item.obvious_missing_content}",
                f"  Obvious color mismatch: {item.obvious_color_mismatch}",
                f"  Obvious ordering issue: {item.obvious_ordering_issue}",
                "  DOM population diagnostic (not a capture gate): "
                f"{item.unique_event_population_valid}",
                f"  Grid geometry valid: {item.grid_geometry_valid}",
                f"  Colors valid: {item.colors_valid}",
                f"  Ordering valid: {item.ordering_valid}",
                f"  Visual match valid: {item.visual_match_valid}",
            ]
        )
    lines.extend(
        [
            "",
            f"Automated overall: {report.automated_result}",
            "Visual contact-sheet approval required: YES",
            "Google Calendar writes: NO",
            "",
        ]
    )
    return "\n".join(lines)


def _hex_rgb(value: str) -> tuple[int, int, int]:
    value = value.lstrip("#")
    return tuple(int(value[index : index + 2], 16) for index in (0, 2, 4))  # type: ignore[return-value]


def _distance(left: tuple[int, ...], right: tuple[int, ...]) -> int:
    return sum((left[index] - right[index]) ** 2 for index in range(3))


def _rgb_css(value: tuple[int, ...]) -> str:
    return f"rgb({value[0]}, {value[1]}, {value[2]})"
