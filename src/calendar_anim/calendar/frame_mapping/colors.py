from dataclasses import dataclass
from typing import Final

from calendar_anim.exceptions import CalendarAnimError

DEFAULT_CALENDAR_BACKGROUND: Final = "#202124"
DEFAULT_CALENDAR_BACKGROUND_COLOR_ID: Final = "8"
DEFAULT_MIN_CONTRAST_RATIO: Final = 2.0


@dataclass(frozen=True)
class CalendarPaletteColor:
    id: str
    hex: str


CALENDAR_EVENT_PALETTE: Final[tuple[CalendarPaletteColor, ...]] = (
    CalendarPaletteColor("1", "#7986CB"),
    CalendarPaletteColor("2", "#33B679"),
    CalendarPaletteColor("3", "#8E24AA"),
    CalendarPaletteColor("4", "#E67C73"),
    CalendarPaletteColor("5", "#F6BF26"),
    CalendarPaletteColor("6", "#F4511E"),
    CalendarPaletteColor("7", "#039BE5"),
    CalendarPaletteColor("8", "#616161"),
    CalendarPaletteColor("9", "#3F51B5"),
    CalendarPaletteColor("10", "#0B8043"),
    CalendarPaletteColor("11", "#D50000"),
)


def calendar_palette_color(color_id: str | None = None) -> CalendarPaletteColor:
    """Resolve an explicit structural background color deterministically."""

    resolved = color_id or DEFAULT_CALENDAR_BACKGROUND_COLOR_ID
    match = next((color for color in CALENDAR_EVENT_PALETTE if color.id == resolved), None)
    if match is None:
        supported = ", ".join(color.id for color in CALENDAR_EVENT_PALETTE)
        raise CalendarAnimError(
            f"Unsupported Calendar background color ID: {resolved!r}. Supported IDs: {supported}"
        )
    return match


def map_calendar_color(
    source_hex: str,
    allowed_color_ids: list[str],
    background_hex: str = DEFAULT_CALENDAR_BACKGROUND,
    minimum_contrast_ratio: float = DEFAULT_MIN_CONTRAST_RATIO,
) -> CalendarPaletteColor:
    """Choose the nearest allowed Calendar color, with a simple contrast fallback."""

    if minimum_contrast_ratio < 1:
        raise CalendarAnimError("minimum contrast ratio must be at least 1")
    allowed = [color for color in CALENDAR_EVENT_PALETTE if color.id in allowed_color_ids]
    unknown = sorted(set(allowed_color_ids) - {color.id for color in CALENDAR_EVENT_PALETTE})
    if unknown:
        raise CalendarAnimError(f"Unsupported Calendar color IDs: {', '.join(unknown)}")
    if not allowed:
        raise CalendarAnimError("Calibration profile has no usable Calendar colors")

    source = _parse_hex(source_hex)
    ranked = sorted(
        allowed,
        key=lambda color: (_squared_distance(source, _parse_hex(color.hex)), int(color.id)),
    )
    nearest = ranked[0]
    if contrast_ratio(nearest.hex, background_hex) >= minimum_contrast_ratio:
        return nearest
    return next(
        (
            color
            for color in ranked[1:]
            if contrast_ratio(color.hex, background_hex) >= minimum_contrast_ratio
        ),
        max(ranked, key=lambda color: (contrast_ratio(color.hex, background_hex), -int(color.id))),
    )


def map_calendar_color_locked(
    source_hex: str, allowed_color_ids: list[str]
) -> CalendarPaletteColor:
    """Map to the nearest frozen color without background-dependent contrast changes."""

    allowed = [color for color in CALENDAR_EVENT_PALETTE if color.id in allowed_color_ids]
    unknown = sorted(set(allowed_color_ids) - {color.id for color in CALENDAR_EVENT_PALETTE})
    if unknown:
        raise CalendarAnimError(f"Unsupported Calendar color IDs: {', '.join(unknown)}")
    if not allowed:
        raise CalendarAnimError("Palette preset has no foreground colors")
    source = _parse_hex(source_hex)
    return min(
        allowed,
        key=lambda color: (_squared_distance(source, _parse_hex(color.hex)), int(color.id)),
    )


def contrast_ratio(first_hex: str, second_hex: str) -> float:
    first = _relative_luminance(_parse_hex(first_hex))
    second = _relative_luminance(_parse_hex(second_hex))
    lighter, darker = max(first, second), min(first, second)
    return (lighter + 0.05) / (darker + 0.05)


def _parse_hex(value: str) -> tuple[int, int, int]:
    normalized = value.strip().lstrip("#")
    if len(normalized) != 6:
        raise CalendarAnimError(f"Invalid RGB color: {value!r}")
    try:
        return tuple(int(normalized[index : index + 2], 16) for index in (0, 2, 4))  # type: ignore[return-value]
    except ValueError as error:
        raise CalendarAnimError(f"Invalid RGB color: {value!r}") from error


def _squared_distance(first: tuple[int, int, int], second: tuple[int, int, int]) -> int:
    return sum((left - right) ** 2 for left, right in zip(first, second, strict=True))


def _relative_luminance(rgb: tuple[int, int, int]) -> float:
    channels = []
    for value in rgb:
        normalized = value / 255
        channels.append(
            normalized / 12.92 if normalized <= 0.04045 else ((normalized + 0.055) / 1.055) ** 2.4
        )
    return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]
