from pathlib import Path
from typing import cast

import cv2
import numpy as np
import numpy.typing as npt

from calendar_anim.exceptions import VideoValidationError

RGBFrame = npt.NDArray[np.uint8]


def read_frames(path: Path, indices: list[int]) -> list[RGBFrame]:
    capture = cv2.VideoCapture(str(path))
    if not capture.isOpened():
        raise VideoValidationError(f"Could not open video: {path}")
    frames: list[RGBFrame] = []
    try:
        for index in indices:
            capture.set(cv2.CAP_PROP_POS_FRAMES, index)
            ok, bgr = capture.read()
            if not ok or bgr is None:
                raise VideoValidationError(f"Could not read source frame {index}")
            frames.append(cast(RGBFrame, cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)))
    finally:
        capture.release()
    return frames
