import numpy as np
import pytest

from calendar_anim.renderer.block_merger import merge_horizontal
from calendar_anim.renderer.palette import palette_colors, quantize
from calendar_anim.renderer.pixelizer import (
    background_mask,
    color_distance,
    final_background_mask,
    parse_hex_color,
)

pytestmark = pytest.mark.unit


def test_quantization_is_deterministic() -> None:
    frame = np.array([[[10, 10, 10], [240, 240, 240]]], dtype=np.uint8)
    first, indices = quantize(frame, "grayscale", 2)
    second, second_indices = quantize(frame, "grayscale", 2)
    assert np.array_equal(first, second)
    assert indices.tolist() == second_indices.tolist() == [[0, 1]]


def test_background_distance_and_mask() -> None:
    frame = np.array([[[0, 0, 0], [30, 40, 0]]], dtype=np.uint8)
    distance = color_distance(frame, parse_hex_color("#000000"))
    assert distance.tolist() == [[0.0, 50.0]]
    assert background_mask(frame, "#000000", 30).tolist() == [[True, False]]
    assert not background_mask(frame, None, 30).any()


def test_final_background_mask_removes_colors_quantized_to_background() -> None:
    source = np.array([[[40, 40, 40], [85, 85, 85]]], dtype=np.uint8)
    quantized = np.array([[[0, 0, 0], [85, 85, 85]]], dtype=np.uint8)

    assert final_background_mask(source, quantized, "#000000", 30).tolist() == [[True, False]]
    assert not final_background_mask(source, quantized, None, 30).any()


def test_horizontal_block_merging() -> None:
    indices = np.array([[0, 0, 0, 1, 1, 2, 2, 0]], dtype=np.int32)
    empty = np.array([[False, False, False, False, False, True, True, False]])
    blocks = merge_horizontal(indices, palette_colors("grayscale", 3), empty)
    assert [(block.x, block.width, block.color_id) for block in blocks] == [
        (0, 3, "0"),
        (3, 2, "1"),
        (7, 1, "0"),
    ]
