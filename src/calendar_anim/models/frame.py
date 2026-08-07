from pydantic import BaseModel, Field, model_validator


class Block(BaseModel):
    x: int = Field(ge=0)
    y: int = Field(ge=0)
    width: int = Field(gt=0)
    height: int = Field(default=1, gt=0)
    color_id: str
    color_hex: str = Field(pattern=r"^#[0-9A-F]{6}$")


class AnimationFrame(BaseModel):
    index: int = Field(ge=0)
    timestamp_seconds: float = Field(ge=0)
    image: str
    blocks: list[Block]

    @model_validator(mode="after")
    def relative_image_path(self) -> "AnimationFrame":
        from pathlib import Path

        path = Path(self.image)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError("frame image must be a safe relative path")
        return self
