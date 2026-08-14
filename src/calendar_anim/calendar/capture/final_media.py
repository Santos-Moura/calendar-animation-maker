import hashlib
import json
import shutil
import subprocess
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

from calendar_anim.exceptions import CalendarAnimError


def media_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as error:
        raise CalendarAnimError(f"Could not hash media file: {path}") from error
    return digest.hexdigest()


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


@dataclass(frozen=True)
class AVMediaProbe:
    video_codec: str
    audio_codec: str
    width: int
    height: int
    fps: float
    video_frame_count: int
    video_duration_seconds: float
    audio_duration_seconds: float
    container_duration_seconds: float
    sample_aspect_ratio: str

    @property
    def av_delta_seconds(self) -> float:
        return abs(self.audio_duration_seconds - self.video_duration_seconds)


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


def build_exact_audio_extract_command(
    tools: FFmpegTools,
    source_video: Path,
    output_audio: Path,
    clip_start: float,
    clip_end: float,
) -> list[str]:
    if clip_start < 0 or clip_end <= clip_start:
        raise CalendarAnimError("Invalid audio clip range")
    duration = clip_end - clip_start
    return [
        str(tools.ffmpeg),
        "-y",
        "-loglevel",
        "error",
        "-ss",
        f"{clip_start:.6f}",
        "-t",
        f"{duration:.6f}",
        "-i",
        str(source_video),
        "-map",
        "0:a:0",
        "-vn",
        "-af",
        f"atrim=duration={duration:.6f},asetpts=PTS-STARTPTS",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
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


def probe_av_media(tools: FFmpegTools, media: Path) -> AVMediaProbe:
    command = [
        str(tools.ffprobe),
        "-v",
        "error",
        "-count_frames",
        "-show_entries",
        (
            "stream=codec_type,codec_name,width,height,avg_frame_rate,nb_frames,"
            "nb_read_frames,duration,sample_aspect_ratio:format=duration"
        ),
        "-of",
        "json",
        str(media),
    ]
    try:
        completed = subprocess.run(command, check=True, capture_output=True, text=True)
        payload = json.loads(completed.stdout)
        streams = payload["streams"]
        video = next(stream for stream in streams if stream["codec_type"] == "video")
        audio = next(stream for stream in streams if stream["codec_type"] == "audio")
        frame_count_text = video.get("nb_read_frames") or video["nb_frames"]
        return AVMediaProbe(
            video_codec=str(video["codec_name"]),
            audio_codec=str(audio["codec_name"]),
            width=int(video["width"]),
            height=int(video["height"]),
            fps=float(Fraction(str(video["avg_frame_rate"]))),
            video_frame_count=int(frame_count_text),
            video_duration_seconds=float(video["duration"]),
            audio_duration_seconds=float(audio["duration"]),
            container_duration_seconds=float(payload["format"]["duration"]),
            sample_aspect_ratio=str(video["sample_aspect_ratio"]),
        )
    except (
        subprocess.CalledProcessError,
        StopIteration,
        IndexError,
        KeyError,
        TypeError,
        ValueError,
    ) as error:
        raise CalendarAnimError(f"ffprobe could not validate final A/V MP4: {media}") from error


def validate_av_media(
    probe: AVMediaProbe,
    resolution: tuple[int, int],
    *,
    expected_duration_seconds: float = 36.0,
    expected_fps: float = 3.0,
    expected_video_frame_count: int = 108,
    tolerance_seconds: float = 0.050,
) -> None:
    failures = []
    if probe.video_codec != "h264":
        failures.append(f"video codec={probe.video_codec}, expected h264")
    if probe.audio_codec != "aac":
        failures.append(f"audio codec={probe.audio_codec}, expected aac")
    if (probe.width, probe.height) != resolution:
        failures.append(
            f"resolution={probe.width}x{probe.height}, expected {resolution[0]}x{resolution[1]}"
        )
    if abs(probe.fps - expected_fps) > 0.001:
        failures.append(f"fps={probe.fps}, expected {expected_fps:g}")
    if probe.video_frame_count != expected_video_frame_count:
        failures.append(
            f"video frames={probe.video_frame_count}, expected {expected_video_frame_count}"
        )
    if abs(probe.video_duration_seconds - expected_duration_seconds) > tolerance_seconds:
        failures.append(
            f"video duration={probe.video_duration_seconds:.6f}s, "
            f"expected {expected_duration_seconds:.6f}s"
        )
    if abs(probe.container_duration_seconds - expected_duration_seconds) > tolerance_seconds:
        failures.append(
            f"container duration={probe.container_duration_seconds:.6f}s, "
            f"expected {expected_duration_seconds:.6f}s"
        )
    if probe.av_delta_seconds > tolerance_seconds:
        failures.append(
            f"A/V delta={probe.av_delta_seconds:.6f}s, maximum {tolerance_seconds:.6f}s"
        )
    if probe.sample_aspect_ratio != "1:1":
        failures.append(f"SAR={probe.sample_aspect_ratio}, expected 1:1")
    if failures:
        raise CalendarAnimError("Final A/V validation failed; " + "; ".join(failures))


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


def extract_exact_audio(
    tools: FFmpegTools,
    source_video: Path,
    output_audio: Path,
    clip_start: float,
    clip_end: float,
) -> Path:
    output_audio.parent.mkdir(parents=True, exist_ok=True)
    _run_ffmpeg(
        build_exact_audio_extract_command(tools, source_video, output_audio, clip_start, clip_end)
    )
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
