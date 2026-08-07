from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

from calendar_anim.exceptions import VideoValidationError

FitMode = Literal["contain", "cover", "stretch"]
PaletteName = Literal["grayscale", "calendar"]


class CropConfig(BaseModel):
    x: int = Field(default=0, ge=0)
    y: int = Field(default=0, ge=0)
    width: int | None = Field(default=None, gt=0)
    height: int | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def complete_dimensions(self) -> "CropConfig":
        if (self.width is None) != (self.height is None):
            raise ValueError("crop width and height must be provided together")
        return self


class RenderConfig(BaseModel):
    animation_id: str = Field(default="animation", pattern=r"^[a-z0-9][a-z0-9-]{0,62}$")
    start_seconds: float = Field(default=0.0, ge=0)
    duration_seconds: float | None = Field(default=None, gt=0)
    frame_count: int = Field(default=10, gt=0)
    grid_width: int = Field(default=28, gt=0, le=512)
    grid_height: int = Field(default=32, gt=0, le=512)
    fit: FitMode = "contain"
    crop: CropConfig = Field(default_factory=CropConfig)
    palette: PaletteName = "calendar"
    colors: int = Field(default=4, ge=2, le=6)
    background: str | None = Field(default=None, pattern=r"^#[0-9A-Fa-f]{6}$")
    background_tolerance: float = Field(default=30.0, ge=0)
    output_fps: float | None = Field(default=None, gt=0)

    @field_validator("colors")
    @classmethod
    def supported_color_count(cls, value: int) -> int:
        if value not in {2, 4, 6}:
            raise ValueError("colors must be one of: 2, 4, 6")
        return value


def load_yaml_config(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise VideoValidationError(f"Configuration file does not exist: {path}")
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise VideoValidationError("Configuration root must be a mapping")
    return data


def config_from_yaml(path: Path) -> RenderConfig:
    raw = load_yaml_config(path)
    animation = raw.get("animation", {})
    clip = raw.get("clip", {})
    grid = raw.get("grid", {})
    palette = raw.get("palette", {})
    preview = raw.get("preview", {})
    crop = raw.get("crop", {})
    return RenderConfig(
        animation_id=animation.get("id", "animation"),
        start_seconds=clip.get("start_seconds", 0.0),
        duration_seconds=clip.get("duration_seconds"),
        frame_count=clip.get("frames", 10),
        grid_width=grid.get("width", 28),
        grid_height=grid.get("height", 32),
        fit=grid.get("fit", "contain"),
        crop=CropConfig(**crop),
        palette=palette.get("name", "calendar"),
        colors=palette.get("colors", 4),
        background=palette.get("background"),
        background_tolerance=palette.get("background_tolerance", 30),
        output_fps=preview.get("fps"),
    )
