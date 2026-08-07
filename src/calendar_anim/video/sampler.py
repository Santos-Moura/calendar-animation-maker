import numpy as np

from calendar_anim.exceptions import VideoValidationError


def resolve_clip(
    start: float, duration: float | None, video_duration: float
) -> tuple[float, float, list[str]]:
    if start < 0:
        raise VideoValidationError("Start time must be non-negative")
    if start >= video_duration:
        raise VideoValidationError("Start time is beyond the end of the video")
    selected = video_duration - start if duration is None else duration
    if selected <= 0:
        raise VideoValidationError("Duration must be greater than zero")
    warnings: list[str] = []
    if start + selected > video_duration:
        selected = video_duration - start
        warnings.append("Requested clip exceeded the video and was clamped to its end")
    if selected <= 0:
        raise VideoValidationError("The requested interval contains no video")
    return start, selected, warnings


def uniform_frame_indices(
    start: float, duration: float, fps: float, count: int, total: int
) -> list[int]:
    if fps <= 0 or count <= 0 or duration <= 0 or total <= 0:
        raise VideoValidationError("FPS, duration, frame count, and total frames must be positive")
    first = max(0, int(np.floor(start * fps)))
    last = min(total - 1, max(first, int(np.ceil((start + duration) * fps)) - 1))
    return [int(value) for value in np.linspace(first, last, num=count, dtype=int)]
