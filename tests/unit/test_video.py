import numpy as np
import pytest

from calendar_anim.config import CropConfig
from calendar_anim.exceptions import VideoValidationError
from calendar_anim.video.processor import crop_frame, resize_to_grid
from calendar_anim.video.sampler import resolve_clip, uniform_frame_indices

pytestmark = pytest.mark.unit


def test_uniform_frame_indices() -> None:
    assert uniform_frame_indices(0, 2, 5, 3, 10) == [0, 4, 9]


def test_clip_is_clamped() -> None:
    start, duration, warnings = resolve_clip(1, 5, 2)
    assert (start, duration) == (1, 1)
    assert warnings


def test_invalid_clip() -> None:
    with pytest.raises(VideoValidationError, match="beyond"):
        resolve_clip(2, 1, 2)


def test_valid_and_invalid_crop() -> None:
    frame = np.zeros((10, 20, 3), dtype=np.uint8)
    assert crop_frame(frame, CropConfig(x=2, y=1, width=5, height=4)).shape == (4, 5, 3)
    with pytest.raises(VideoValidationError, match="exceeds"):
        crop_frame(frame, CropConfig(x=18, y=0, width=5, height=4))


@pytest.mark.parametrize("fit", ["contain", "cover", "stretch"])
def test_resize_to_grid(fit: str) -> None:
    frame = np.full((10, 20, 3), 255, dtype=np.uint8)
    assert resize_to_grid(frame, 8, 8, fit).shape == (8, 8, 3)  # type: ignore[arg-type]
