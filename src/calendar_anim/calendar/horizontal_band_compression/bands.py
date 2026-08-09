from collections.abc import Sequence

from calendar_anim.calendar.frame_mapping.models import CalendarMappedCell, CellRole
from calendar_anim.calendar.horizontal_band_compression.models import (
    HorizontalBandSlot,
    SynchronizedHorizontalBand,
)
from calendar_anim.exceptions import CalendarAnimError


def build_synchronized_horizontal_bands(
    cells: Sequence[CalendarMappedCell],
    width: int,
    height: int,
    columns_per_day: int,
    days_used: int,
) -> tuple[list[SynchronizedHorizontalBand], list[int]]:
    """Group equal consecutive row vectors without crossing a day boundary."""

    if width != columns_per_day * days_used:
        raise CalendarAnimError(
            f"Horizontal-band grid width {width} does not equal "
            f"{days_used} days x {columns_per_day} columns"
        )
    expected_cells = width * height
    if len(cells) != expected_cells:
        raise CalendarAnimError(
            f"Horizontal-band compression requires a complete {width}x{height} canvas; "
            f"received {len(cells)} cells instead of {expected_cells}"
        )
    by_coordinate = {(cell.logical_x, cell.logical_y): cell for cell in cells}
    if len(by_coordinate) != expected_cells:
        raise CalendarAnimError("Horizontal-band compression canvas contains duplicate coordinates")

    bands: list[SynchronizedHorizontalBand] = []
    bands_per_day: list[int] = []
    for day_offset in range(days_used):
        day_bands: list[SynchronizedHorizontalBand] = []
        start_y = 0
        current = _row_signature(by_coordinate, day_offset, 0, columns_per_day)
        for logical_y in range(1, height + 1):
            next_signature = (
                _row_signature(by_coordinate, day_offset, logical_y, columns_per_day)
                if logical_y < height
                else None
            )
            if next_signature == current:
                continue
            day_bands.append(
                SynchronizedHorizontalBand(
                    day_offset=day_offset,
                    start_y=start_y,
                    length=logical_y - start_y,
                    slots=[
                        HorizontalBandSlot(
                            subcolumn=slot,
                            color_id=color_id,
                            cell_role=cell_role,
                        )
                        for slot, (color_id, cell_role) in enumerate(current)
                    ],
                )
            )
            if next_signature is not None:
                start_y = logical_y
                current = next_signature
        bands.extend(day_bands)
        bands_per_day.append(len(day_bands))
    return bands, bands_per_day


def _row_signature(
    cells: dict[tuple[int, int], CalendarMappedCell],
    day_offset: int,
    logical_y: int,
    columns_per_day: int,
) -> tuple[tuple[str, CellRole], ...]:
    start_x = day_offset * columns_per_day
    signature: list[tuple[str, CellRole]] = []
    for subcolumn in range(columns_per_day):
        coordinate = (start_x + subcolumn, logical_y)
        try:
            cell = cells[coordinate]
        except KeyError as error:
            raise CalendarAnimError(
                f"Horizontal-band compression canvas is missing cell {coordinate}"
            ) from error
        signature.append((cell.color_id, cell.cell_role))
    return tuple(signature)
