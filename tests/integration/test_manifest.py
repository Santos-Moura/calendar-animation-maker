from pathlib import Path

import pytest

from calendar_anim.models.frame import Block
from calendar_anim.renderer.manifest import read_manifest, validate_manifest_files, write_manifest
from tests.factories import make_manifest

pytestmark = pytest.mark.integration


def test_manifest_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "animation.json"
    write_manifest(make_manifest(), path)
    assert read_manifest(path) == make_manifest()


def test_manifest_detects_out_of_bounds_block(tmp_path: Path) -> None:
    frames = tmp_path / "frames"
    frames.mkdir()
    (frames / "frame_000.png").touch()
    manifest = make_manifest(Block(x=3, y=0, width=2, color_id="0", color_hex="#000000"))
    assert any(
        "width" in error for error in validate_manifest_files(manifest, tmp_path / "animation.json")
    )
