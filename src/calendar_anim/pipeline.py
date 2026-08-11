import hashlib
import json
from pathlib import Path

from calendar_anim.config import RenderConfig
from calendar_anim.models.animation import (
    AnimationManifest,
    AnimationStatistics,
    RenderInfo,
    SourceInfo,
)
from calendar_anim.models.frame import AnimationFrame
from calendar_anim.models.video import VideoInfo
from calendar_anim.renderer.block_merger import merge_horizontal
from calendar_anim.renderer.manifest import write_manifest
from calendar_anim.renderer.palette import palette_colors, quantize
from calendar_anim.renderer.pixelizer import final_background_mask
from calendar_anim.renderer.preview import save_frame, save_gif
from calendar_anim.video.inspector import inspect_video
from calendar_anim.video.processor import crop_frame, resize_to_grid
from calendar_anim.video.reader import read_frames
from calendar_anim.video.sampler import resolve_clip, uniform_frame_indices


def render_video(
    video_path: Path, output_dir: Path, config: RenderConfig
) -> tuple[AnimationManifest, VideoInfo, list[str]]:
    info = inspect_video(video_path)
    start, duration, warnings = resolve_clip(
        config.start_seconds, config.duration_seconds, info.duration_seconds
    )
    indices = uniform_frame_indices(
        start, duration, info.fps, config.frame_count, info.total_frames
    )
    timestamps = [index / info.fps for index in indices]
    source_frames = read_frames(info.path, indices)
    output_dir.mkdir(parents=True, exist_ok=True)
    frames_dir = output_dir / "frames"
    frames_dir.mkdir(exist_ok=True)
    processed = []
    masks = []
    manifest_frames: list[AnimationFrame] = []
    non_empty = 0
    colors = palette_colors(config.palette, config.colors)
    for index, (source, timestamp) in enumerate(zip(source_frames, timestamps, strict=True)):
        grid = resize_to_grid(
            crop_frame(source, config.crop), config.grid_width, config.grid_height, config.fit
        )
        quantized, color_indices = quantize(grid, config.palette, config.colors)
        empty = final_background_mask(
            grid,
            quantized,
            config.background,
            config.background_tolerance,
        )
        blocks = merge_horizontal(color_indices, colors, empty)
        relative_path = f"frames/frame_{index:03d}.png"
        save_frame(quantized, empty, output_dir / relative_path)
        processed.append(quantized)
        masks.append(empty)
        non_empty += int((~empty).sum())
        manifest_frames.append(
            AnimationFrame(
                index=index, timestamp_seconds=timestamp, image=relative_path, blocks=blocks
            )
        )
    output_fps = config.output_fps or max(1.0, config.frame_count / duration)
    save_gif(processed, masks, output_dir / "preview.gif", output_fps)
    block_count = sum(len(frame.blocks) for frame in manifest_frames)
    manifest = AnimationManifest(
        animation_id=config.animation_id,
        source=SourceInfo(
            file_name=info.path.name,
            sha256=_sha256(info.path),
            start_seconds=start,
            duration_seconds=duration,
            source_fps=info.fps,
        ),
        render=RenderInfo(
            frame_count=config.frame_count,
            output_fps=output_fps,
            grid_width=config.grid_width,
            grid_height=config.grid_height,
            fit=config.fit,
            palette=config.palette,
            colors=config.colors,
            background=config.background,
            background_tolerance=config.background_tolerance,
        ),
        statistics=AnimationStatistics(
            non_empty_pixels=non_empty, blocks=block_count, estimated_events=block_count
        ),
        frames=manifest_frames,
    )
    write_manifest(manifest, output_dir / "animation.json")
    (output_dir / "source-info.json").write_text(
        json.dumps(info.model_dump(mode="json"), indent=2) + "\n", encoding="utf-8"
    )
    return manifest, info, [*info.warnings, *warnings]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
