import json
from pathlib import Path

from pydantic import ValidationError

from calendar_anim.exceptions import ManifestValidationError
from calendar_anim.models.animation import AnimationManifest


def write_manifest(manifest: AnimationManifest, path: Path) -> None:
    path.write_text(manifest.model_dump_json(indent=2) + "\n", encoding="utf-8")


def read_manifest(path: Path) -> AnimationManifest:
    try:
        return AnimationManifest.model_validate_json(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise ManifestValidationError(f"Manifest does not exist: {path}") from error
    except (ValidationError, json.JSONDecodeError) as error:
        raise ManifestValidationError(f"Invalid manifest schema: {error}") from error


def validate_manifest_files(manifest: AnimationManifest, manifest_path: Path) -> list[str]:
    errors: list[str] = []
    if manifest.schema_version != "1.0":
        errors.append(f"Unsupported schema version: {manifest.schema_version}")
    if len(manifest.frames) != manifest.render.frame_count:
        errors.append("frame_count does not match frames array")
    for expected, frame in enumerate(manifest.frames):
        if frame.index != expected:
            errors.append(f"Frame index {frame.index} should be {expected}")
        if not (manifest_path.parent / frame.image).is_file():
            errors.append(f"Missing frame image: {frame.image}")
        for block_index, block in enumerate(frame.blocks):
            if block.x + block.width > manifest.render.grid_width:
                errors.append(f"Frame {frame.index} block {block_index} exceeds grid width")
            if block.y + block.height > manifest.render.grid_height:
                errors.append(f"Frame {frame.index} block {block_index} exceeds grid height")
    block_count = sum(len(frame.blocks) for frame in manifest.frames)
    if manifest.statistics.blocks != block_count:
        errors.append("statistics.blocks does not match frame blocks")
    return errors
