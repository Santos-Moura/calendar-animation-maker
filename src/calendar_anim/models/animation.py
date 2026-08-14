from pydantic import BaseModel, Field

from calendar_anim.models.frame import AnimationFrame


class SourceInfo(BaseModel):
    file_name: str
    sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    start_seconds: float = Field(ge=0)
    duration_seconds: float = Field(gt=0)
    source_fps: float = Field(gt=0)


class RenderInfo(BaseModel):
    frame_count: int = Field(gt=0)
    output_fps: float = Field(gt=0)
    grid_width: int = Field(gt=0)
    grid_height: int = Field(gt=0)
    fit: str
    palette: str
    colors: int = Field(gt=0)
    background: str | None = None
    background_tolerance: float = Field(ge=0)


class AnimationStatistics(BaseModel):
    non_empty_pixels: int = Field(ge=0)
    blocks: int = Field(ge=0)
    estimated_events: int = Field(ge=0)


class AnimationManifest(BaseModel):
    schema_version: str = "1.0"
    animation_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,62}$")
    source: SourceInfo
    render: RenderInfo
    statistics: AnimationStatistics
    frames: list[AnimationFrame]
