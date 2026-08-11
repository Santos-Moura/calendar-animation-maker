import shutil
import subprocess
from pathlib import Path
from typing import cast

from PIL import Image

from calendar_anim.calendar.capture.artifacts import CaptureStore
from calendar_anim.calendar.capture.models import CapturePlan, CaptureState, FrameCaptureStatus
from calendar_anim.calendar.capture.service import captured_paths
from calendar_anim.exceptions import CalendarAnimError

PIXEL_ART_H264_CRF = 10
PIXEL_ART_H264_PRESET = "slow"


def validate_completed_capture(
    plan: CapturePlan, state: CaptureState, store: CaptureStore
) -> list[Path]:
    incomplete = [
        frame.frame_index
        for frame in state.frames
        if frame.status is not FrameCaptureStatus.COMPLETED
    ]
    if incomplete:
        indexes = ", ".join(str(index) for index in incomplete)
        raise CalendarAnimError(f"Composition requires completed captures: {indexes}")
    paths = captured_paths(plan, store)
    missing = [str(path) for path in paths if not path.is_file()]
    if missing:
        raise CalendarAnimError("Captured screenshots are missing: " + ", ".join(missing))
    return paths


def compose_gif(frame_paths: list[Path], output_path: Path, fps: float) -> Path:
    if not frame_paths:
        raise CalendarAnimError("Cannot compose an empty capture")
    if fps <= 0:
        raise CalendarAnimError("Composition FPS must be positive")
    frames: list[Image.Image] = []
    try:
        for sequence, path in enumerate(frame_paths):
            with Image.open(path) as source:
                frames.append(_gif_frame(source.convert("RGB"), sequence))
        dimensions = {frame.size for frame in frames}
        if len(dimensions) != 1:
            raise CalendarAnimError("Captured screenshots do not have consistent dimensions")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        duration_ms = max(1, round(1000 / fps))
        frames[0].save(
            output_path,
            format="GIF",
            save_all=True,
            append_images=frames[1:],
            duration=[duration_ms] * len(frames),
            loop=0,
            optimize=False,
            disposal=2,
        )
    finally:
        for frame in frames:
            frame.close()
    return output_path


def _gif_frame(source: Image.Image, sequence: int) -> Image.Image:
    """Keep visually identical adjacent frames distinct to preserve their timeline slots."""
    frame = source.quantize(colors=254)
    palette = frame.getpalette()
    if palette is None:
        raise CalendarAnimError("Could not build a GIF palette")
    marker_color = cast(tuple[int, int, int], source.getpixel((0, 0)))
    for palette_index in (254, 255):
        offset = palette_index * 3
        palette[offset : offset + 3] = list(marker_color)
    frame.putpalette(palette)
    frame.putpixel((0, 0), 254 + (sequence % 2))
    source.close()
    return frame


def compose_mp4(
    frame_paths: list[Path], output_path: Path, fps: float, *, pixel_scale: int = 1
) -> Path:
    if not frame_paths:
        raise CalendarAnimError("Cannot compose an empty capture")
    if fps <= 0:
        raise CalendarAnimError("Composition FPS must be positive")
    executable = shutil.which("ffmpeg")
    if executable is None:
        raise CalendarAnimError("ffmpeg was not found; install it or compose only the GIF")
    command = build_mp4_command(executable, frame_paths, output_path, fps, pixel_scale=pixel_scale)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run(command, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as error:
        detail = error.stderr.strip() or str(error)
        raise CalendarAnimError(f"ffmpeg failed: {detail}") from error
    return output_path


def build_mp4_command(
    executable: str,
    frame_paths: list[Path],
    output_path: Path,
    fps: float,
    *,
    pixel_scale: int = 1,
) -> list[str]:
    if not frame_paths:
        raise CalendarAnimError("Cannot compose an empty capture")
    if fps <= 0:
        raise CalendarAnimError("Composition FPS must be positive")
    if pixel_scale < 1:
        raise CalendarAnimError("Pixel scale must be a positive integer")
    first_index = _frame_index(frame_paths[0])
    expected = list(range(first_index, first_index + len(frame_paths)))
    actual = [_frame_index(path) for path in frame_paths]
    if actual != expected:
        raise CalendarAnimError("MP4 composition requires consecutive capture frame filenames")
    dimensions = {_image_dimensions(path) for path in frame_paths}
    if len(dimensions) != 1:
        raise CalendarAnimError("Captured screenshots do not have consistent dimensions")
    source_width, source_height = next(iter(dimensions))
    output_width = source_width * pixel_scale
    output_height = source_height * pixel_scale
    if output_width % 2 or output_height % 2:
        raise CalendarAnimError(
            "Pixel-perfect H.264 yuv420p output requires even dimensions; "
            f"resolved output is {output_width}x{output_height}"
        )
    input_pattern = frame_paths[0].parent / "frame-%04d.png"
    return [
        executable,
        "-y",
        "-loglevel",
        "error",
        "-framerate",
        str(fps),
        "-start_number",
        str(first_index),
        "-i",
        str(input_pattern),
        "-frames:v",
        str(len(frame_paths)),
        "-c:v",
        "libx264",
        "-preset",
        PIXEL_ART_H264_PRESET,
        "-crf",
        str(PIXEL_ART_H264_CRF),
        "-pix_fmt",
        "yuv420p",
        "-vf",
        f"scale={output_width}:{output_height}:flags=neighbor,setsar=1",
        str(output_path),
    ]


def _image_dimensions(path: Path) -> tuple[int, int]:
    try:
        with Image.open(path) as image:
            return image.size
    except OSError as error:
        raise CalendarAnimError(f"Unable to read capture frame: {path}") from error


def _frame_index(path: Path) -> int:
    try:
        return int(path.stem.removeprefix("frame-"))
    except ValueError as error:
        raise CalendarAnimError(f"Invalid capture frame filename: {path.name}") from error
