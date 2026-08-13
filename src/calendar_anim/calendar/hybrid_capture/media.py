import json
import subprocess
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

from PIL import Image

from calendar_anim.calendar.capture.composition import (
    PIXEL_ART_H264_CRF,
    PIXEL_ART_H264_PRESET,
)
from calendar_anim.calendar.capture.final_media import FFmpegTools
from calendar_anim.exceptions import CalendarAnimError

FINAL_FRAME_COUNT = 108
FINAL_FPS = 3.0


@dataclass(frozen=True)
class FinalFrameSequence:
    directory: Path
    paths: tuple[Path, ...]
    resolution: tuple[int, int]

    @property
    def count(self) -> int:
        return len(self.paths)

    @property
    def first(self) -> Path:
        return self.paths[0]

    @property
    def last(self) -> Path:
        return self.paths[-1]


@dataclass(frozen=True)
class FinalVisualProbe:
    codec: str
    profile: str
    width: int
    height: int
    fps: float
    frame_count: int
    duration_seconds: float
    sample_aspect_ratio: str


def inspect_final_frames(
    directory: Path,
    resolution: tuple[int, int] = (504, 288),
    *,
    frame_count: int = FINAL_FRAME_COUNT,
) -> FinalFrameSequence:
    if frame_count <= 0:
        raise CalendarAnimError("Final frame count must be positive")
    expected = tuple(directory / f"frame_{index:03d}.png" for index in range(frame_count))
    expected_names = {path.name for path in expected}
    actual_pngs = tuple(sorted(directory.glob("*.png"), key=lambda path: path.name))
    actual_names = {path.name for path in actual_pngs}
    missing = sorted(expected_names - actual_names)
    unexpected = sorted(actual_names - expected_names)
    if missing or unexpected:
        details = []
        if missing:
            details.append("missing: " + ", ".join(missing))
        if unexpected:
            details.append("unexpected: " + ", ".join(unexpected))
        raise CalendarAnimError("Invalid final frame sequence; " + "; ".join(details))
    for path in expected:
        try:
            with Image.open(path) as image:
                if image.size != resolution:
                    raise CalendarAnimError(
                        f"Final frame is not {resolution[0]}x{resolution[1]}: {path}"
                    )
        except OSError as error:
            raise CalendarAnimError(f"Unreadable final frame: {path}") from error
    return FinalFrameSequence(directory, expected, resolution)


def validate_final_frames(directory: Path, resolution: tuple[int, int] = (504, 288)) -> list[Path]:
    return list(inspect_final_frames(directory, resolution).paths)


def build_final_visual_command(
    tools: FFmpegTools,
    frame_directory: Path,
    output: Path,
    *,
    frame_count: int = FINAL_FRAME_COUNT,
    fps: float = FINAL_FPS,
) -> list[str]:
    return [
        str(tools.ffmpeg),
        "-y",
        "-loglevel",
        "error",
        "-framerate",
        f"{fps:g}",
        "-start_number",
        "0",
        "-i",
        str(frame_directory / "frame_%03d.png"),
        "-frames:v",
        str(frame_count),
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


def compose_final_visual(
    tools: FFmpegTools,
    frame_directory: Path,
    output: Path,
    resolution: tuple[int, int] = (504, 288),
    *,
    frame_count: int = FINAL_FRAME_COUNT,
    fps: float = FINAL_FPS,
) -> Path:
    inspect_final_frames(frame_directory, resolution, frame_count=frame_count)
    output.parent.mkdir(parents=True, exist_ok=True)
    _run(
        build_final_visual_command(
            tools,
            frame_directory,
            output,
            frame_count=frame_count,
            fps=fps,
        )
    )
    return output


def probe_final_visual(tools: FFmpegTools, media: Path) -> FinalVisualProbe:
    command = [
        str(tools.ffprobe),
        "-v",
        "error",
        "-count_frames",
        "-select_streams",
        "v:0",
        "-show_entries",
        (
            "stream=codec_name,profile,width,height,avg_frame_rate,nb_frames,"
            "nb_read_frames,sample_aspect_ratio:format=duration"
        ),
        "-of",
        "json",
        str(media),
    ]
    try:
        completed = subprocess.run(command, check=True, capture_output=True, text=True)
        payload = json.loads(completed.stdout)
        stream = payload["streams"][0]
        frame_count_text = stream.get("nb_read_frames") or stream["nb_frames"]
        return FinalVisualProbe(
            codec=str(stream["codec_name"]),
            profile=str(stream["profile"]),
            width=int(stream["width"]),
            height=int(stream["height"]),
            fps=float(Fraction(str(stream["avg_frame_rate"]))),
            frame_count=int(frame_count_text),
            duration_seconds=float(payload["format"]["duration"]),
            sample_aspect_ratio=str(stream["sample_aspect_ratio"]),
        )
    except (subprocess.CalledProcessError, IndexError, KeyError, TypeError, ValueError) as error:
        raise CalendarAnimError(f"ffprobe could not validate final visual MP4: {media}") from error


def validate_final_visual_probe(
    probe: FinalVisualProbe,
    resolution: tuple[int, int],
    *,
    expected_frame_count: int = FINAL_FRAME_COUNT,
    expected_fps: float = FINAL_FPS,
    expected_duration_seconds: float | None = None,
    duration_tolerance_seconds: float = 0.05,
) -> None:
    expected_duration = expected_duration_seconds or expected_frame_count / expected_fps
    failures = []
    if probe.codec != "h264":
        failures.append(f"codec={probe.codec}, expected h264")
    if probe.profile.lower() != "high":
        failures.append(f"profile={probe.profile}, expected High")
    if (probe.width, probe.height) != resolution:
        failures.append(
            f"resolution={probe.width}x{probe.height}, expected {resolution[0]}x{resolution[1]}"
        )
    if abs(probe.fps - expected_fps) > 0.001:
        failures.append(f"fps={probe.fps}, expected {expected_fps}")
    if probe.frame_count != expected_frame_count:
        failures.append(f"frames={probe.frame_count}, expected {expected_frame_count}")
    if abs(probe.duration_seconds - expected_duration) > duration_tolerance_seconds:
        failures.append(
            f"duration={probe.duration_seconds:.6f}s, expected {expected_duration:.6f}s"
        )
    if probe.sample_aspect_ratio != "1:1":
        failures.append(f"SAR={probe.sample_aspect_ratio}, expected 1:1")
    if failures:
        raise CalendarAnimError("Final visual MP4 validation failed; " + "; ".join(failures))


def _run(command: list[str]) -> None:
    try:
        subprocess.run(command, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as error:
        raise CalendarAnimError(error.stderr.strip() or str(error)) from error
