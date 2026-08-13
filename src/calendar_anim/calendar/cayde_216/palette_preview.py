import json
import statistics
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any

from PIL import Image, ImageColor, ImageDraw, ImageFont

from calendar_anim.calendar.calibration.profile import DEFAULT_PROFILE_PATH, load_profile
from calendar_anim.calendar.cayde_216.artifacts import Cayde216Store, write_atomic
from calendar_anim.calendar.cayde_216.planner import (
    MAX_EVENTS_PER_FRAME,
    RUN_ID,
    SOURCE_MANIFEST_RELATIVE,
    protected_hashes,
)
from calendar_anim.calendar.frame_mapping.colors import (
    calendar_palette_color,
    contrast_ratio,
)
from calendar_anim.calendar.frame_mapping.mapper import build_single_frame_plan
from calendar_anim.calendar.frame_mapping.models import (
    CellRole,
    EventCompressionMode,
    FrameMappingMode,
    SingleFrameCalendarPlan,
)
from calendar_anim.calendar.high_detail import apply_high_detail_grid
from calendar_anim.calendar.palette_presets import CAYDE_216_CANDIDATES
from calendar_anim.calendar.subcolumn_ordering import SubcolumnOrderStrategy
from calendar_anim.exceptions import CalendarAnimError
from calendar_anim.renderer.manifest import read_manifest

REPRESENTATIVE_FRAME_INDICES = (0, 31, 61, 92, 123, 154, 184, 215)
PREVIEW_SIZE = (504, 288)
SINGLE_FRAME_FILENAMES = {
    "cayde-lilac-pop": "lilac-pop",
    "cayde-indigo-flare": "indigo-flare",
    "cayde-cyan-magenta": "cyan-magenta",
}


def build_palette_previews(
    *,
    store: Cayde216Store | None = None,
    profile_path: Path = DEFAULT_PROFILE_PATH,
) -> tuple[dict[str, Any], list[Path]]:
    """Render three isolated Calendar-palette candidates without selecting a winner."""

    store = store or Cayde216Store()
    before = protected_hashes()
    run_directory = store.run_directory(RUN_ID)
    manifest_path = run_directory / SOURCE_MANIFEST_RELATIVE
    manifest = read_manifest(manifest_path)
    if len(manifest.frames) != 216:
        raise CalendarAnimError("Palette preview requires the genuine 216-frame manifest")
    profile = apply_high_detail_grid(load_profile(profile_path), "126x72")
    output = run_directory / "palette-candidates"
    output.mkdir(parents=True, exist_ok=True)
    timestamps = [
        manifest.frames[index].timestamp_seconds for index in REPRESENTATIVE_FRAME_INDICES
    ]
    source_sheet = _source_contact_sheet(
        manifest_path, manifest, output / "source-contact-sheet.png"
    )
    artifacts: list[Path] = [source_sheet]
    candidates: list[dict[str, Any]] = []
    comparison_rows: list[tuple[str, list[Image.Image]]] = []

    for preset in CAYDE_216_CANDIDATES:
        directory = output / preset.name
        frames_directory = directory / "frames"
        frames_directory.mkdir(parents=True, exist_ok=True)
        images: list[Image.Image] = []
        color_counts: Counter[str] = Counter()
        event_counts: list[int] = []
        foreground_cells = 0
        for frame_index in REPRESENTATIVE_FRAME_INDICES:
            plan = build_single_frame_plan(
                manifest,
                profile,
                frame_index=frame_index,
                anchor_date=date(2034, 1, 1),
                run_id=f"{preset.name}-preview-{frame_index:03d}",
                max_execute_events=MAX_EVENTS_PER_FRAME,
                calendar_name="Calendar Animation Lab B",
                mapping_mode=FrameMappingMode.FULL_GRID,
                event_compression=EventCompressionMode.SYNCHRONIZED_HORIZONTAL_BANDS,
                palette_preset=preset.name,
                subcolumn_order_strategy=SubcolumnOrderStrategy.ZERO_WIDTH,
            )
            image = _logical_image(plan)
            path = frames_directory / f"frame_{frame_index:03d}.png"
            image.save(path)
            artifacts.append(path)
            images.append(image)
            event_counts.append(plan.event_count)
            for cell in plan.mapped_cells:
                color_counts[cell.color_id] += 1
                if cell.cell_role is CellRole.FOREGROUND:
                    foreground_cells += 1
        gif = directory / "preview.gif"
        images[0].save(
            gif,
            save_all=True,
            append_images=images[1:],
            duration=500,
            loop=0,
            optimize=False,
        )
        sheet = _candidate_contact_sheet(preset.name, images, directory / "contact-sheet.png")
        artifacts.extend((gif, sheet))
        comparison_rows.append((preset.name, images))
        background = calendar_palette_color(preset.background_color_id)
        foreground = [calendar_palette_color(color_id) for color_id in preset.foreground_color_ids]
        contrasts = [contrast_ratio(background.hex, color.hex) for color in foreground]
        candidates.append(
            {
                "name": preset.name,
                "artistic_intent": preset.artistic_intent,
                "background": {"color_id": background.id, "hex": background.hex},
                "foreground": [
                    {
                        "color_id": color.id,
                        "hex": color.hex,
                        "contrast_vs_background": ratio,
                    }
                    for color, ratio in zip(foreground, contrasts, strict=True)
                ],
                "minimum_foreground_contrast": min(contrasts),
                "mean_foreground_contrast": statistics.fmean(contrasts),
                "mapped_color_cell_counts": dict(sorted(color_counts.items())),
                "sample_foreground_cells": foreground_cells,
                "compressed_events_per_sample_frame": event_counts,
                "artifacts": {
                    "gif": str(gif),
                    "contact_sheet": str(sheet),
                    "frames": str(frames_directory),
                },
            }
        )

    comparison = _comparison_contact_sheet(comparison_rows, output / "palette-comparison.png")
    artifacts.append(comparison)
    after = protected_hashes()
    if before != after:
        raise CalendarAnimError("Protected 108-frame artifacts changed during palette preview")
    report: dict[str, Any] = {
        "schema_version": "1.0",
        "run_id": RUN_ID,
        "purpose": "candidate comparison only",
        "representative_frame_indices": list(REPRESENTATIVE_FRAME_INDICES),
        "representative_timestamps_seconds": timestamps,
        "preview_size": list(PREVIEW_SIZE),
        "mapping_deterministic": True,
        "palette_locked_per_candidate": True,
        "final_palette_selected": False,
        "final_run_replanned": False,
        "candidates": candidates,
        "preliminary_recommendation": "cayde-lilac-pop",
        "comparison": str(comparison),
        "source_contact_sheet": str(source_sheet),
        "old_artifacts_unchanged": True,
        "google_calendar_reads": False,
        "google_calendar_writes": False,
    }
    report_json = write_atomic(output / "report.json", json.dumps(report, indent=2) + "\n")
    report_text = write_atomic(output / "report.txt", _report_text(report))
    artifacts.extend((report_json, report_text))
    return report, artifacts


def build_single_frame_palette_comparison(
    *,
    human_frame: int = 93,
    store: Cayde216Store | None = None,
) -> tuple[dict[str, Any], list[Path]]:
    """Reuse generated candidate PNGs for one isolated three-palette comparison."""

    if not 1 <= human_frame <= 216:
        raise CalendarAnimError("Palette preview frame must be between 1 and 216")
    store = store or Cayde216Store()
    before = protected_hashes()
    frame_index = human_frame - 1
    run_directory = store.run_directory(RUN_ID)
    manifest = read_manifest(run_directory / SOURCE_MANIFEST_RELATIVE)
    if len(manifest.frames) != 216:
        raise CalendarAnimError("Single-frame palette preview requires the 216-frame manifest")
    output = run_directory / "palette-single-frame"
    output.mkdir(parents=True, exist_ok=True)
    images: list[tuple[str, Image.Image]] = []
    artifacts: list[Path] = []
    image_paths: dict[str, str] = {}
    for preset in CAYDE_216_CANDIDATES:
        short_name = SINGLE_FRAME_FILENAMES[preset.name]
        source = (
            run_directory
            / "palette-candidates"
            / preset.name
            / "frames"
            / f"frame_{frame_index:03d}.png"
        )
        if not source.is_file():
            raise CalendarAnimError(
                f"Generated candidate frame is missing; run palette preview first: {source}"
            )
        destination = output / f"{short_name}-frame-{human_frame:03d}.png"
        with Image.open(source) as opened:
            image = opened.convert("RGB")
            if image.size != PREVIEW_SIZE:
                raise CalendarAnimError(
                    f"Candidate frame {source} has size {image.size}, expected {PREVIEW_SIZE}"
                )
            image.save(destination)
            images.append((short_name, image.copy()))
        artifacts.append(destination)
        image_paths[short_name] = str(destination)
    comparison = _single_frame_contact_sheet(
        human_frame, images, output / f"palette-comparison-frame-{human_frame:03d}.png"
    )
    artifacts.append(comparison)
    after = protected_hashes()
    if before != after:
        raise CalendarAnimError("Protected 108-frame artifacts changed during palette comparison")
    report: dict[str, Any] = {
        "schema_version": "1.0",
        "run_id": RUN_ID,
        "human_frame": human_frame,
        "frame_index": frame_index,
        "timestamp_seconds": manifest.frames[frame_index].timestamp_seconds,
        "resolution": list(PREVIEW_SIZE),
        "same_geometry": True,
        "source_render_reused": True,
        "candidate_previews_reused": True,
        "images": image_paths,
        "comparison": str(comparison),
        "recommendation": "cayde-lilac-pop",
        "google_calendar_reads": False,
        "google_calendar_writes": False,
        "upload": False,
        "browser_capture": False,
        "old_artifacts_unchanged": True,
    }
    report_path = write_atomic(
        output / f"report-frame-{human_frame:03d}.json", json.dumps(report, indent=2) + "\n"
    )
    artifacts.append(report_path)
    return report, artifacts


def _logical_image(plan: SingleFrameCalendarPlan) -> Image.Image:
    image = Image.new("RGB", (126, 72), "#202124")
    for cell in plan.mapped_cells:
        image.putpixel((cell.logical_x, cell.logical_y), ImageColor.getrgb(cell.color_hex))
    return image.resize(PREVIEW_SIZE, Image.Resampling.NEAREST)


def _source_contact_sheet(manifest_path: Path, manifest: object, output: Path) -> Path:
    images = []
    for index in REPRESENTATIVE_FRAME_INDICES:
        source = manifest_path.parent / manifest.frames[index].image  # type: ignore[attr-defined]
        with Image.open(source) as image:
            images.append(image.convert("RGB").resize(PREVIEW_SIZE, Image.Resampling.NEAREST))
    return _candidate_contact_sheet("source quantized frames", images, output)


def _candidate_contact_sheet(name: str, images: list[Image.Image], output: Path) -> Path:
    thumb = (252, 144)
    label_height = 28
    canvas = Image.new("RGB", (thumb[0] * 4, (thumb[1] + label_height) * 2), "#202124")
    draw = ImageDraw.Draw(canvas)
    font = ImageFont.load_default()
    for offset, (frame_index, image) in enumerate(
        zip(REPRESENTATIVE_FRAME_INDICES, images, strict=True)
    ):
        column, row = offset % 4, offset // 4
        left, top = column * thumb[0], row * (thumb[1] + label_height)
        canvas.paste(image.resize(thumb, Image.Resampling.NEAREST), (left, top + label_height))
        draw.text((left + 6, top + 8), f"{name} | frame {frame_index + 1}", fill="white", font=font)
    canvas.save(output)
    return output


def _comparison_contact_sheet(rows: list[tuple[str, list[Image.Image]]], output: Path) -> Path:
    thumb = (252, 144)
    label_width = 150
    header = 26
    canvas = Image.new(
        "RGB",
        (label_width + thumb[0] * len(REPRESENTATIVE_FRAME_INDICES), header + thumb[1] * len(rows)),
        "#202124",
    )
    draw = ImageDraw.Draw(canvas)
    font = ImageFont.load_default()
    for column, frame_index in enumerate(REPRESENTATIVE_FRAME_INDICES):
        draw.text(
            (label_width + column * thumb[0] + 6, 8),
            f"frame {frame_index + 1}",
            fill="white",
            font=font,
        )
    for row, (name, images) in enumerate(rows):
        top = header + row * thumb[1]
        draw.text((8, top + 12), name, fill="white", font=font)
        for column, image in enumerate(images):
            canvas.paste(
                image.resize(thumb, Image.Resampling.NEAREST),
                (label_width + column * thumb[0], top),
            )
    canvas.save(output)
    return output


def _single_frame_contact_sheet(
    human_frame: int,
    images: list[tuple[str, Image.Image]],
    output: Path,
) -> Path:
    label_height = 34
    canvas = Image.new(
        "RGB",
        (PREVIEW_SIZE[0] * len(images), PREVIEW_SIZE[1] + label_height),
        "#202124",
    )
    draw = ImageDraw.Draw(canvas)
    font = ImageFont.load_default()
    for column, (name, image) in enumerate(images):
        left = column * PREVIEW_SIZE[0]
        draw.text((left + 8, 11), f"{name} | frame {human_frame}", fill="white", font=font)
        canvas.paste(image, (left, label_height))
    canvas.save(output)
    return output


def _report_text(report: dict[str, Any]) -> str:
    lines = [
        "CAYDE 216 PALETTE CANDIDATES",
        "============================",
        "",
        "Final palette selected: NO",
        "Final run replanned: NO",
        "Mapping deterministic: YES",
        "Google Calendar reads: NO",
        "Google Calendar writes: NO",
        "",
    ]
    for candidate in report["candidates"]:
        lines.extend(
            [
                str(candidate["name"]),
                "-" * len(str(candidate["name"])),
                f"Intent: {candidate['artistic_intent']}",
                f"Background: {candidate['background']}",
                f"Foreground: {candidate['foreground']}",
                f"Minimum contrast: {candidate['minimum_foreground_contrast']:.3f}",
                f"Mean contrast: {candidate['mean_foreground_contrast']:.3f}",
                f"Artifacts: {candidate['artifacts']}",
                "",
            ]
        )
    lines.extend(
        [
            f"Preliminary recommendation: {report['preliminary_recommendation']}",
            "User visual approval required before replanning: YES",
            "",
        ]
    )
    return "\n".join(lines)
