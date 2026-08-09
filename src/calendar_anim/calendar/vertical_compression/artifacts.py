from pathlib import Path

from calendar_anim.calendar.vertical_compression.models import (
    AnimationVerticalCompressionEstimate,
)


def build_vertical_compression_report(
    estimate: AnimationVerticalCompressionEstimate,
) -> str:
    lines = [
        "Vertical Compression Estimate",
        "=============================",
        "",
        f"Animation ID: {estimate.animation_id}",
        f"Grid: {estimate.grid_width}x{estimate.grid_height}",
        f"Frames: {len(estimate.frames)}",
        "",
    ]
    for frame in estimate.frames:
        lines.extend(
            [
                f"Frame {frame.frame_index}",
                f"Baseline: {frame.baseline_events}",
                f"Compressed runs: {frame.compressed_runs}",
                f"Saved: {frame.saved_events}",
                f"Reduction: {frame.reduction_percent:.1f}%",
                f"Foreground runs: {frame.foreground_runs}",
                f"Background runs: {frame.background_runs}",
                f"Longest vertical run: {frame.longest_vertical_run}",
                f"Average run length: {frame.average_run_length:.3f}",
                "",
            ]
        )
    lines.extend(
        [
            "Total",
            "-----",
            f"Baseline: {estimate.total_baseline_events}",
            f"Compressed runs: {estimate.total_compressed_runs}",
            f"Saved: {estimate.total_saved_events}",
            f"Reduction: {estimate.total_reduction_percent:.1f}%",
            f"Foreground runs: {estimate.total_foreground_runs}",
            f"Background runs: {estimate.total_background_runs}",
            f"Longest vertical run: {estimate.longest_vertical_run}",
            f"Average run length: {estimate.average_run_length:.3f}",
            "",
            "This is a local estimate only. The production mapper and Calendar events "
            "are unchanged.",
            "",
        ]
    )
    return "\n".join(lines)


def write_vertical_compression_artifacts(
    estimate: AnimationVerticalCompressionEstimate, output_dir: Path
) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    report_path = output_dir / "vertical-compression-estimate.txt"
    report_path.write_text(build_vertical_compression_report(estimate), encoding="utf-8")
    json_path = output_dir / "vertical-compression-estimate.json"
    json_path.write_text(estimate.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return report_path, json_path
