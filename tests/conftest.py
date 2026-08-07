from pathlib import Path

import cv2
import numpy as np
import pytest


@pytest.fixture
def tiny_video(tmp_path: Path) -> Path:
    path = tmp_path / "tiny.avi"
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), 5.0, (16, 12))
    if not writer.isOpened():
        pytest.skip("OpenCV video writer codec is unavailable")
    for index in range(5):
        frame = np.zeros((12, 16, 3), dtype=np.uint8)
        frame[:, : 3 + index] = (20 * index, 80, 220)
        writer.write(frame)
    writer.release()
    return path
