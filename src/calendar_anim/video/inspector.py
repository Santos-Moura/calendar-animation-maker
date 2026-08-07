from pathlib import Path

import cv2

from calendar_anim.exceptions import VideoValidationError
from calendar_anim.models.video import VideoInfo

ACCEPTED_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".gif"}


def inspect_video(path: Path) -> VideoInfo:
    path = path.expanduser().resolve()
    if not path.is_file():
        raise VideoValidationError(f"Video file does not exist: {path}")
    extension = path.suffix.lower()
    if extension not in ACCEPTED_EXTENSIONS:
        accepted = ", ".join(sorted(ACCEPTED_EXTENSIONS))
        raise VideoValidationError(
            f"Unsupported video extension {extension!r}; use one of: {accepted}"
        )
    capture = cv2.VideoCapture(str(path))
    if not capture.isOpened():
        capture.release()
        raise VideoValidationError(f"OpenCV could not open video: {path}")
    try:
        fps = float(capture.get(cv2.CAP_PROP_FPS))
        total = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
        width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
        codec_int = int(capture.get(cv2.CAP_PROP_FOURCC))
        codec = "".join(chr((codec_int >> (8 * i)) & 0xFF) for i in range(4)).strip("\x00")
        if fps <= 0:
            raise VideoValidationError("Video reports an invalid FPS value")
        if total <= 0 or width <= 0 or height <= 0:
            raise VideoValidationError("Video has no readable frames or invalid dimensions")
        warnings: list[str] = []
        if extension != ".mp4":
            warnings.append("MP4/H.264 is the recommended input format")
        return VideoInfo(
            path=path,
            extension=extension,
            codec=codec or None,
            width=width,
            height=height,
            fps=fps,
            total_frames=total,
            duration_seconds=total / fps,
            warnings=warnings,
        )
    finally:
        capture.release()
