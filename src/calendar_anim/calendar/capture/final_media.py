import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from calendar_anim.exceptions import CalendarAnimError


@dataclass(frozen=True)
class FFmpegTools:
    ffmpeg: Path
    ffprobe: Path
    version: str


@dataclass(frozen=True)
class MediaTiming:
    visual_seconds: float
    audio_seconds: float
    final_seconds: float

    @property
    def difference_seconds(self) -> float:
        return self.final_seconds - self.visual_seconds


def detect_ffmpeg() -> FFmpegTools:
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    if ffmpeg is None or ffprobe is None:
        raise CalendarAnimError(
            "ffmpeg and ffprobe are required for final MP4/audio composition; "
            "install them explicitly and ensure both are on PATH"
        )
    completed = subprocess.run([ffmpeg, "-version"], check=True, capture_output=True, text=True)
    version = completed.stdout.splitlines()[0] if completed.stdout else "unknown"
    return FFmpegTools(Path(ffmpeg), Path(ffprobe), version)


def frame_sequence_duration(frame_count: int, fps: float) -> float:
    if frame_count <= 0:
        raise CalendarAnimError("Frame count must be positive")
    if fps <= 0:
        raise CalendarAnimError("FPS must be positive")
    return frame_count / fps


def build_extract_audio_command(
    tools: FFmpegTools,
    source_video: Path,
    output_audio: Path,
    clip_start: float,
    clip_end: float,
    *,
    copy_aac: bool,
) -> list[str]:
    if clip_start < 0 or clip_end <= clip_start:
        raise CalendarAnimError("Invalid audio clip range")
    codec = ["-c:a", "copy"] if copy_aac else ["-c:a", "aac", "-b:a", "192k"]
    return [
        str(tools.ffmpeg),
        "-y",
        "-loglevel",
        "error",
        "-ss",
        f"{clip_start:.6f}",
        "-t",
        f"{clip_end - clip_start:.6f}",
        "-i",
        str(source_video),
        "-map",
        "0:a:0",
        "-vn",
        *codec,
        str(output_audio),
    ]


def build_mux_command(
    tools: FFmpegTools, visual_mp4: Path, audio: Path, output_mp4: Path
) -> list[str]:
    return [
        str(tools.ffmpeg),
        "-y",
        "-loglevel",
        "error",
        "-i",
        str(visual_mp4),
        "-i",
        str(audio),
        "-map",
        "0:v:0",
        "-map",
        "1:a:0",
        "-c:v",
        "copy",
        "-c:a",
        "copy",
        "-shortest",
        str(output_mp4),
    ]


def probe_duration(tools: FFmpegTools, media: Path) -> float:
    completed = subprocess.run(
        [
            str(tools.ffprobe),
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "json",
            str(media),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    try:
        return float(json.loads(completed.stdout)["format"]["duration"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise CalendarAnimError(f"ffprobe returned no duration for {media}") from error


def probe_audio_codec(tools: FFmpegTools, media: Path) -> str:
    completed = subprocess.run(
        [
            str(tools.ffprobe),
            "-v",
            "error",
            "-select_streams",
            "a:0",
            "-show_entries",
            "stream=codec_name",
            "-of",
            "json",
            str(media),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    try:
        return str(json.loads(completed.stdout)["streams"][0]["codec_name"])
    except (IndexError, KeyError, TypeError, json.JSONDecodeError) as error:
        raise CalendarAnimError(f"No audio stream was found in {media}") from error


def extract_audio(
    tools: FFmpegTools,
    source_video: Path,
    output_audio: Path,
    clip_start: float,
    clip_end: float,
    *,
    source_audio_codec: str,
) -> Path:
    output_audio.parent.mkdir(parents=True, exist_ok=True)
    command = build_extract_audio_command(
        tools,
        source_video,
        output_audio,
        clip_start,
        clip_end,
        copy_aac=source_audio_codec.lower() == "aac",
    )
    _run_ffmpeg(command)
    return output_audio


def mux_audio(tools: FFmpegTools, visual_mp4: Path, audio: Path, output_mp4: Path) -> Path:
    output_mp4.parent.mkdir(parents=True, exist_ok=True)
    _run_ffmpeg(build_mux_command(tools, visual_mp4, audio, output_mp4))
    return output_mp4


def validate_timing(
    visual_seconds: float,
    audio_seconds: float,
    final_seconds: float,
    *,
    tolerance_seconds: float = 0.050,
) -> MediaTiming:
    timing = MediaTiming(visual_seconds, audio_seconds, final_seconds)
    if abs(audio_seconds - visual_seconds) > tolerance_seconds:
        raise CalendarAnimError(
            "Audio/visual duration mismatch is "
            f"{abs(audio_seconds - visual_seconds) * 1000:.1f} ms, above "
            f"{tolerance_seconds * 1000:.1f} ms"
        )
    if abs(final_seconds - visual_seconds) > tolerance_seconds:
        raise CalendarAnimError(
            "Final mux duration mismatch is "
            f"{abs(final_seconds - visual_seconds) * 1000:.1f} ms, above "
            f"{tolerance_seconds * 1000:.1f} ms"
        )
    return timing


def _run_ffmpeg(command: list[str]) -> None:
    try:
        subprocess.run(command, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as error:
        detail = error.stderr.strip() or str(error)
        raise CalendarAnimError(f"ffmpeg failed: {detail}") from error
