from typing import Final

import numpy as np
import numpy.typing as npt

from calendar_anim.config import PaletteName
from calendar_anim.video.reader import RGBFrame

CALENDAR_PALETTE: Final[npt.NDArray[np.uint8]] = np.array(
    [
        [121, 134, 203],
        [51, 182, 121],
        [66, 133, 244],
        [142, 36, 170],
        [230, 124, 115],
        [246, 191, 38],
    ],
    dtype=np.uint8,
)


def palette_colors(name: PaletteName, count: int) -> npt.NDArray[np.uint8]:
    if not 2 <= count <= 6:
        raise ValueError("colors must be between 2 and 6")
    if name == "grayscale":
        values = np.linspace(0, 255, count, dtype=np.uint8)
        return np.column_stack((values, values, values)).astype(np.uint8)
    return CALENDAR_PALETTE[:count].copy()


def quantize(
    frame: RGBFrame, name: PaletteName, count: int
) -> tuple[RGBFrame, npt.NDArray[np.int32]]:
    colors = palette_colors(name, count)
    pixels = frame.astype(np.int32)
    distance = np.sum(
        (pixels[:, :, None, :] - colors.astype(np.int32)[None, None, :, :]) ** 2, axis=3
    )
    indices = np.argmin(distance, axis=2).astype(np.int32)
    return colors[indices], indices


def color_hex(rgb: npt.NDArray[np.uint8]) -> str:
    return f"#{int(rgb[0]):02X}{int(rgb[1]):02X}{int(rgb[2]):02X}"
