import numpy as np
import numpy.typing as npt

from calendar_anim.models.frame import Block
from calendar_anim.renderer.palette import color_hex


def merge_horizontal(
    color_indices: npt.NDArray[np.int32],
    colors: npt.NDArray[np.uint8],
    empty: npt.NDArray[np.bool_],
) -> list[Block]:
    height, width = color_indices.shape
    blocks: list[Block] = []
    for y in range(height):
        x = 0
        while x < width:
            if empty[y, x]:
                x += 1
                continue
            color_index = int(color_indices[y, x])
            end = x + 1
            while end < width and not empty[y, end] and int(color_indices[y, end]) == color_index:
                end += 1
            blocks.append(
                Block(
                    x=x,
                    y=y,
                    width=end - x,
                    color_id=f"{color_index}",
                    color_hex=color_hex(colors[color_index]),
                )
            )
            x = end
    return blocks
