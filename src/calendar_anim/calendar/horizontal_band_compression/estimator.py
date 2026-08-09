from collections.abc import Sequence
from datetime import date

from calendar_anim.calendar.calibration.models import CalibrationProfile
from calendar_anim.calendar.frame_mapping.mapper import build_single_frame_plan
from calendar_anim.calendar.frame_mapping.models import (
    CalendarMappedCell,
    CellRole,
    FrameMappingMode,
)
from calendar_anim.calendar.frame_mapping.service import ABSOLUTE_SINGLE_FRAME_MAX_EVENTS
from calendar_anim.calendar.horizontal_band_compression.bands import (
    build_synchronized_horizontal_bands,
)
from calendar_anim.calendar.horizontal_band_compression.models import (
    AnimationHorizontalBandEstimate,
    FrameHorizontalBandEstimate,
)
from calendar_anim.exceptions import CalendarAnimError
from calendar_anim.models.animation import AnimationManifest

ESTIMATE_ANCHOR_DATE = date(2026, 1, 4)


def estimate_synchronized_horizontal_bands(
    cells: Sequence[CalendarMappedCell],
    width: int,
    height: int,
    columns_per_day: int,
    days_used: int,
    frame_index: int,
) -> FrameHorizontalBandEstimate:
    expected_cells = width * height
    bands, bands_per_day = build_synchronized_horizontal_bands(
        cells, width, height, columns_per_day, days_used
    )

    compressed_events = len(bands) * columns_per_day
    saved = expected_cells - compressed_events
    return FrameHorizontalBandEstimate(
        frame_index=frame_index,
        baseline_events=expected_cells,
        band_count=len(bands),
        compressed_events=compressed_events,
        saved_events=saved,
        reduction_percent=_percentage(saved, expected_cells),
        foreground_events=sum(
            slot.cell_role is CellRole.FOREGROUND for band in bands for slot in band.slots
        ),
        background_events=sum(
            slot.cell_role is CellRole.BACKGROUND for band in bands for slot in band.slots
        ),
        longest_band_rows=max((band.length for band in bands), default=0),
        average_band_length=(round((days_used * height) / len(bands), 3) if bands else 0),
        bands_per_day=bands_per_day,
        bands=bands,
    )


def estimate_manifest_horizontal_bands(
    manifest: AnimationManifest,
    profile: CalibrationProfile,
    calendar_background_color_id: str | None = None,
) -> AnimationHorizontalBandEstimate:
    frame_estimates: list[FrameHorizontalBandEstimate] = []
    for frame in manifest.frames:
        plan = build_single_frame_plan(
            manifest,
            profile,
            frame_index=frame.index,
            anchor_date=ESTIMATE_ANCHOR_DATE,
            run_id=f"band-estimate-{frame.index:04d}",
            max_execute_events=ABSOLUTE_SINGLE_FRAME_MAX_EVENTS,
            mapping_mode=FrameMappingMode.FULL_GRID,
            calendar_background_color_id=calendar_background_color_id,
        )
        frame_estimates.append(
            estimate_synchronized_horizontal_bands(
                plan.mapped_cells,
                plan.target_grid_width,
                plan.target_grid_height,
                plan.columns_per_day,
                plan.days_used,
                frame.index,
            )
        )

    if not frame_estimates:
        raise CalendarAnimError("Manifest contains no frames")
    baseline = sum(frame.baseline_events for frame in frame_estimates)
    compressed = sum(frame.compressed_events for frame in frame_estimates)
    saved = baseline - compressed
    total_bands = sum(frame.band_count for frame in frame_estimates)
    total_day_rows = (
        len(frame_estimates)
        * profile.horizontal_mapping.days_used
        * (profile.candidate_grid.height or 0)
    )
    return AnimationHorizontalBandEstimate(
        animation_id=manifest.animation_id,
        grid_width=profile.candidate_grid.width or 0,
        grid_height=profile.candidate_grid.height or 0,
        columns_per_day=profile.horizontal_mapping.usable_overlap_columns_per_day or 0,
        days_used=profile.horizontal_mapping.days_used,
        frames=frame_estimates,
        total_baseline_events=baseline,
        total_compressed_events=compressed,
        total_saved_events=saved,
        total_reduction_percent=_percentage(saved, baseline),
        total_bands=total_bands,
        total_foreground_events=sum(frame.foreground_events for frame in frame_estimates),
        total_background_events=sum(frame.background_events for frame in frame_estimates),
        longest_band_rows=max(frame.longest_band_rows for frame in frame_estimates),
        average_band_length=round(total_day_rows / total_bands, 3) if total_bands else 0,
    )


def _percentage(part: int, whole: int) -> float:
    return round((part / whole) * 100, 1) if whole else 0
