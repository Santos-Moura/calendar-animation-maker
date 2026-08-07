from pathlib import Path

import numpy as np
from PIL import Image

from calendar_anim.video.reader import RGBFrame


def save_frame(frame: RGBFrame, empty: np.ndarray, path: Path, scale: int = 12) -> None:
    rgba = np.dstack((frame, np.where(empty, 0, 255).astype(np.uint8)))
    image = Image.fromarray(rgba, mode="RGBA").resize(
        (frame.shape[1] * scale, frame.shape[0] * scale), Image.Resampling.NEAREST
    )
    image.save(path)


def save_gif(
    frames: list[RGBFrame], empty_masks: list[np.ndarray], path: Path, fps: float, scale: int = 12
) -> None:
    images: list[Image.Image] = []
    for frame, empty in zip(frames, empty_masks, strict=True):
        rendered = frame.copy()
        rendered[empty] = (0, 0, 0)
        images.append(
            Image.fromarray(rendered, mode="RGB").resize(
                (frame.shape[1] * scale, frame.shape[0] * scale), Image.Resampling.NEAREST
            )
        )
    images[0].save(
        path,
        save_all=True,
        append_images=images[1:],
        duration=max(1, round(1000 / fps)),
        loop=0,
        optimize=False,
    )
