from dataclasses import dataclass
from typing import Final

from calendar_anim.exceptions import CalendarAnimError


@dataclass(frozen=True)
class CalendarPalettePreset:
    name: str
    background_color_id: str
    foreground_color_ids: tuple[str, ...]
    artistic_intent: str


CAYDE_FINAL: Final = CalendarPalettePreset(
    name="cayde-final",
    background_color_id="1",
    foreground_color_ids=("1", "2", "3", "4"),
    artistic_intent=(
        "Approved artistic palette: blue-lilac background with purple-magenta Cayde "
        "and fixed green/coral accents."
    ),
)

PALETTE_PRESETS: Final = {CAYDE_FINAL.name: CAYDE_FINAL}


def resolve_palette_preset(name: str | None) -> CalendarPalettePreset | None:
    if name is None:
        return None
    normalized = name.strip().lower()
    try:
        return PALETTE_PRESETS[normalized]
    except KeyError as error:
        supported = ", ".join(sorted(PALETTE_PRESETS))
        raise CalendarAnimError(
            f"Unsupported Calendar palette preset: {name!r}. Supported presets: {supported}"
        ) from error
