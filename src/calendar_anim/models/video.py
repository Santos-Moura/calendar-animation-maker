from pathlib import Path

from pydantic import BaseModel, Field


class VideoInfo(BaseModel):
    path: Path
    extension: str
    codec: str | None = None
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    fps: float = Field(gt=0)
    total_frames: int = Field(gt=0)
    duration_seconds: float = Field(gt=0)
    has_audio: bool | None = None
    warnings: list[str] = Field(default_factory=list)
