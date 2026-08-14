from dataclasses import dataclass
from typing import Final

from calendar_anim.exceptions import CalendarAnimError


@dataclass(frozen=True)
class CalendarPalettePreset:
    name: str
    background_color_id: str
    foreground_color_ids: tuple[str, ...]
    artistic_intent: str
    source_background_hexes: tuple[str, ...] = ()


CAYDE_FINAL: Final = CalendarPalettePreset(
    name="cayde-final",
    background_color_id="1",
    foreground_color_ids=("1", "2", "3", "4"),
    artistic_intent=(
        "Approved artistic palette: blue-lilac background with purple-magenta Cayde "
        "and fixed green/coral accents."
    ),
)

CAYDE_CYAN_MAGENTA: Final = CalendarPalettePreset(
    name="cayde-cyan-magenta",
    background_color_id="7",
    foreground_color_ids=("3", "5", "9", "11"),
    artistic_intent=(
        "Approved 216-frame palette: cold cyan canvas with purple, gold, indigo, "
        "and red foreground."
    ),
    source_background_hexes=("#7986CB",),
)

PALETTE_PRESETS: Final = {
    preset.name: preset for preset in (CAYDE_FINAL, CAYDE_CYAN_MAGENTA)
}


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
