import subprocess
from pathlib import Path

from PIL import Image

from calendar_anim.calendar.capture.composition import (
    PIXEL_ART_H264_CRF,
    PIXEL_ART_H264_PRESET,
)
from calendar_anim.calendar.capture.final_media import FFmpegTools
from calendar_anim.exceptions import CalendarAnimError


def validate_final_frames(directory: Path) -> list[Path]:
    expected = [directory / f"frame_{index:03d}.png" for index in range(108)]
    actual = sorted(directory.glob("frame_*.png"))
    if actual != expected:
        raise CalendarAnimError("Final composition requires exactly frame_000.png-frame_107.png")
    for path in expected:
        try:
            with Image.open(path) as image:
                if image.size != (504, 288):
                    raise CalendarAnimError(f"Final frame is not 504x288: {path}")
        except OSError as error:
            raise CalendarAnimError(f"Unreadable final frame: {path}") from error
    return expected


def build_final_visual_command(
    tools: FFmpegTools, frame_directory: Path, output: Path
) -> list[str]:
    return [
        str(tools.ffmpeg),
        "-y",
        "-loglevel",
        "error",
        "-framerate",
        "3",
        "-start_number",
        "0",
        "-i",
        str(frame_directory / "frame_%03d.png"),
        "-frames:v",
        "108",
        "-c:v",
        "libx264",
        "-profile:v",
        "high",
        "-preset",
        PIXEL_ART_H264_PRESET,
        "-crf",
        str(PIXEL_ART_H264_CRF),
        "-pix_fmt",
        "yuv420p",
        "-vf",
        "setsar=1",
        str(output),
    ]


def compose_final_visual(tools: FFmpegTools, frame_directory: Path, output: Path) -> Path:
    validate_final_frames(frame_directory)
    output.parent.mkdir(parents=True, exist_ok=True)
    _run(build_final_visual_command(tools, frame_directory, output))
    return output


def _run(command: list[str]) -> None:
    try:
        subprocess.run(command, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as error:
        raise CalendarAnimError(error.stderr.strip() or str(error)) from error
