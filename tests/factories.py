from calendar_anim.models.animation import (
    AnimationManifest,
    AnimationStatistics,
    RenderInfo,
    SourceInfo,
)
from calendar_anim.models.frame import AnimationFrame, Block


def make_manifest(block: Block | None = None) -> AnimationManifest:
    blocks = [block or Block(x=0, y=0, width=2, color_id="0", color_hex="#000000")]
    return AnimationManifest(
        animation_id="test-animation",
        source=SourceInfo(file_name="tiny.avi", start_seconds=0, duration_seconds=1, source_fps=5),
        render=RenderInfo(
            frame_count=1,
            output_fps=1,
            grid_width=4,
            grid_height=4,
            fit="contain",
            palette="grayscale",
            colors=2,
            background=None,
            background_tolerance=30,
        ),
        statistics=AnimationStatistics(non_empty_pixels=2, blocks=1, estimated_events=1),
        frames=[
            AnimationFrame(
                index=0, timestamp_seconds=0, image="frames/frame_000.png", blocks=blocks
            )
        ],
    )
