import shutil
import subprocess
from pathlib import Path

from PIL import Image

from calendar_anim.calendar.capture.artifacts import CaptureStore
from calendar_anim.calendar.capture.models import CapturePlan, CaptureState, FrameCaptureStatus
from calendar_anim.calendar.capture.service import captured_paths
from calendar_anim.exceptions import CalendarAnimError


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
        for path in frame_paths:
            with Image.open(path) as source:
                frames.append(source.convert("RGB"))
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


def compose_mp4(frame_paths: list[Path], output_path: Path, fps: float) -> Path:
    if not frame_paths:
        raise CalendarAnimError("Cannot compose an empty capture")
    if fps <= 0:
        raise CalendarAnimError("Composition FPS must be positive")
    executable = shutil.which("ffmpeg")
    if executable is None:
        raise CalendarAnimError("ffmpeg was not found; install it or compose only the GIF")
    first_index = _frame_index(frame_paths[0])
    expected = list(range(first_index, first_index + len(frame_paths)))
    actual = [_frame_index(path) for path in frame_paths]
    if actual != expected:
        raise CalendarAnimError("MP4 composition requires consecutive capture frame filenames")
    input_pattern = frame_paths[0].parent / "frame-%04d.png"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    command = [
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
        "-pix_fmt",
        "yuv420p",
        "-vf",
        "pad=ceil(iw/2)*2:ceil(ih/2)*2",
        str(output_path),
    ]
    try:
        subprocess.run(command, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as error:
        detail = error.stderr.strip() or str(error)
        raise CalendarAnimError(f"ffmpeg failed: {detail}") from error
    return output_path


def _frame_index(path: Path) -> int:
    try:
        return int(path.stem.removeprefix("frame-"))
    except ValueError as error:
        raise CalendarAnimError(f"Invalid capture frame filename: {path.name}") from error
