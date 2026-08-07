from typing import cast

import cv2
import numpy as np

from calendar_anim.config import CropConfig, FitMode
from calendar_anim.exceptions import VideoValidationError
from calendar_anim.video.reader import RGBFrame


def crop_frame(frame: RGBFrame, crop: CropConfig) -> RGBFrame:
    height, width = frame.shape[:2]
    if crop.width is None or crop.height is None:
        return frame.copy()
    if crop.x + crop.width > width or crop.y + crop.height > height:
        raise VideoValidationError(
            f"Crop ({crop.x}, {crop.y}, {crop.width}, {crop.height}) exceeds frame {width}x{height}"
        )
    return frame[crop.y : crop.y + crop.height, crop.x : crop.x + crop.width].copy()


def resize_to_grid(frame: RGBFrame, width: int, height: int, fit: FitMode) -> RGBFrame:
    source_h, source_w = frame.shape[:2]
    if width <= 0 or height <= 0:
        raise VideoValidationError("Grid dimensions must be positive")
    if fit == "stretch":
        return cast(RGBFrame, cv2.resize(frame, (width, height), interpolation=cv2.INTER_AREA))
    scale = (
        min(width / source_w, height / source_h)
        if fit == "contain"
        else max(width / source_w, height / source_h)
    )
    resized_w = max(1, round(source_w * scale))
    resized_h = max(1, round(source_h * scale))
    resized = cv2.resize(frame, (resized_w, resized_h), interpolation=cv2.INTER_AREA)
    if fit == "cover":
        x = (resized_w - width) // 2
        y = (resized_h - height) // 2
        return cast(RGBFrame, resized[y : y + height, x : x + width].copy())
    canvas = np.zeros((height, width, 3), dtype=np.uint8)
    x = (width - resized_w) // 2
    y = (height - resized_h) // 2
    canvas[y : y + resized_h, x : x + resized_w] = resized
    return canvas
