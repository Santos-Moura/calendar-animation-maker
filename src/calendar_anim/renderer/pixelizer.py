import numpy as np
import numpy.typing as npt

from calendar_anim.video.reader import RGBFrame


def parse_hex_color(value: str) -> npt.NDArray[np.uint8]:
    value = value.lstrip("#")
    return np.array([int(value[index : index + 2], 16) for index in (0, 2, 4)], dtype=np.uint8)


def color_distance(frame: RGBFrame, color: npt.NDArray[np.uint8]) -> npt.NDArray[np.float64]:
    delta = frame.astype(np.float64) - color.astype(np.float64)
    return np.sqrt(np.sum(delta * delta, axis=2))


def background_mask(
    frame: RGBFrame, background: str | None, tolerance: float
) -> npt.NDArray[np.bool_]:
    if background is None:
        return np.zeros(frame.shape[:2], dtype=np.bool_)
    return color_distance(frame, parse_hex_color(background)) <= tolerance
