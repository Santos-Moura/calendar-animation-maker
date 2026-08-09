from pathlib import Path

from calendar_anim.calendar.horizontal_band_compression.models import (
    AnimationHorizontalBandEstimate,
)


def build_horizontal_band_report(estimate: AnimationHorizontalBandEstimate) -> str:
    lines = [
        "Synchronized Horizontal-Band Compression Estimate",
        "=================================================",
        "",
        f"Animation ID: {estimate.animation_id}",
        f"Grid: {estimate.grid_width}x{estimate.grid_height}",
        f"Columns per day: {estimate.columns_per_day}",
        f"Days: {estimate.days_used}",
        f"Frames: {len(estimate.frames)}",
        "",
    ]
    for frame in estimate.frames:
        lines.extend(
            [
                f"Frame {frame.frame_index}",
                f"Baseline: {frame.baseline_events}",
                f"Bands: {frame.band_count}",
                f"Bands per day: {', '.join(str(value) for value in frame.bands_per_day)}",
                f"Compressed events: {frame.compressed_events}",
                f"Saved: {frame.saved_events}",
                f"Reduction: {frame.reduction_percent:.1f}%",
                f"Foreground events: {frame.foreground_events}",
                f"Background events: {frame.background_events}",
                f"Longest band: {frame.longest_band_rows} rows",
                f"Average band length: {frame.average_band_length:.3f} rows",
                "",
            ]
        )
    lines.extend(
        [
            "Total",
            "-----",
            f"Baseline: {estimate.total_baseline_events}",
            f"Bands: {estimate.total_bands}",
            f"Compressed events: {estimate.total_compressed_events}",
            f"Saved: {estimate.total_saved_events}",
            f"Reduction: {estimate.total_reduction_percent:.1f}%",
            f"Foreground events: {estimate.total_foreground_events}",
            f"Background events: {estimate.total_background_events}",
            f"Longest band: {estimate.longest_band_rows} rows",
            f"Average band length: {estimate.average_band_length:.3f} rows",
            "",
            "Every band keeps all six subcolumns synchronized to the same start and end.",
            "This is a local estimate only; the production mapper remains unchanged.",
            "",
        ]
    )
    return "\n".join(lines)


def write_horizontal_band_artifacts(
    estimate: AnimationHorizontalBandEstimate, output_dir: Path
) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    report_path = output_dir / "horizontal-band-compression-estimate.txt"
    report_path.write_text(build_horizontal_band_report(estimate), encoding="utf-8")
    json_path = output_dir / "horizontal-band-compression-estimate.json"
    json_path.write_text(estimate.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return report_path, json_path
