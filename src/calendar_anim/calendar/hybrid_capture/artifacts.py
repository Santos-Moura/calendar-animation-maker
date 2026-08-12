import os
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from PIL import Image, ImageDraw

from calendar_anim.calendar.frame_mapping.models import SingleFrameCalendarPlan
from calendar_anim.calendar.hybrid_capture.models import (
    HybridCapturePlan,
    HybridCaptureState,
    HybridFrameState,
    HybridSanityReport,
    HybridSeamReport,
)
from calendar_anim.exceptions import CalendarAnimError


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

    def state_path(self, run_id: str) -> Path:
        return self.run_directory(run_id) / "final-capture-state.json"

    def sanity_directory(self, run_id: str) -> Path:
        return self.run_directory(run_id) / "sanity"

    def sanity_frame_directory(self, run_id: str, human_frame: int) -> Path:
        return self.sanity_directory(run_id) / f"frame-{human_frame:03d}"

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

    def final_frames_directory(self, run_id: str) -> Path:
        return self.run_directory(run_id) / "final-frames"

    def final_frame_path(self, run_id: str, frame_index: int) -> Path:
        return self.final_frames_directory(run_id) / f"frame_{frame_index:03d}.png"

    def final_raw_path(self, run_id: str, frame_index: int) -> Path:
        return self.run_directory(run_id) / "final-capture" / "raw" / f"frame_{frame_index:03d}.png"

    def final_logical_path(self, run_id: str, frame_index: int) -> Path:
        return (
            self.run_directory(run_id)
            / "final-capture"
            / "logical"
            / f"frame_{frame_index:03d}.png"
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

    def initialize_state(self, plan: HybridCapturePlan) -> HybridCaptureState:
        path = self.state_path(plan.run_id)
        if path.exists():
            state = HybridCaptureState.model_validate_json(path.read_text(encoding="utf-8"))
            expected = [(item.frame_index, item.calendar_profile) for item in plan.frames]
            actual = [(item.frame_index, item.profile) for item in state.frames]
            if actual != expected:
                raise CalendarAnimError("Hybrid capture state differs from locked frame boundary")
            return state
        state = HybridCaptureState(
            run_id=plan.run_id,
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
        return write_atomic(self.state_path(state.run_id), state.model_dump_json(indent=2) + "\n")

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
    image = Image.new("RGB", (1008, 324), "#202124")
    draw = ImageDraw.Draw(image)
    draw.text((10, 8), "FRAME 23 / ACCOUNT A", fill="white")
    draw.text((514, 8), "FRAME 24 / ACCOUNT B", fill="white")
    for x, path in ((0, left), (504, right)):
        with Image.open(path) as source:
            if source.size != (504, 288):
                raise CalendarAnimError("A/B seam inputs must both be 504x288")
            image.paste(source.convert("RGB"), (x, 36))
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
                f"  Capture retry cycles: {item.capture_retry_cycles}",
                f"  Capture error: {item.capture_error or 'none'}",
                f"  Normalized geometry: {item.normalized_width}x{item.normalized_height}",
                f"  Logical cell match: {item.logical_cell_match_ratio:.3%}",
                f"  Obvious missing content: {item.obvious_missing_content}",
                f"  Obvious color mismatch: {item.obvious_color_mismatch}",
                f"  Obvious ordering issue: {item.obvious_ordering_issue}",
                f"  Population valid: {item.unique_event_population_valid}",
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
