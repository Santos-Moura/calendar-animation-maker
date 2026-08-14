from pathlib import Path

import pytest

from calendar_anim.calendar.capture.final_media import (
    AVMediaProbe,
    FFmpegTools,
    build_exact_audio_extract_command,
    build_extract_audio_command,
    build_mux_command,
    frame_sequence_duration,
    validate_av_media,
    validate_timing,
)
from calendar_anim.exceptions import CalendarAnimError

pytestmark = pytest.mark.unit


def _tools() -> FFmpegTools:
    return FFmpegTools(Path("ffmpeg"), Path("ffprobe"), "ffmpeg test")


@pytest.mark.parametrize(("frames", "fps"), [(72, 2.0), (108, 3.0)])
def test_final_frame_sequences_are_exactly_36_seconds(frames: int, fps: float) -> None:
    assert frame_sequence_duration(frames, fps) == 36.0


def test_audio_extract_uses_exact_114_to_150_range_and_copies_aac() -> None:
    command = build_extract_audio_command(
        _tools(),
        Path("input.mp4"),
        Path("cutscene-audio.m4a"),
        114.0,
        150.0,
        copy_aac=True,
    )

    assert command[command.index("-ss") + 1] == "114.000000"
    assert command[command.index("-t") + 1] == "36.000000"
    assert command[command.index("-c:a") + 1] == "copy"


def test_non_aac_audio_uses_mp4_compatible_aac_encoding() -> None:
    command = build_extract_audio_command(
        _tools(), Path("input.mp4"), Path("audio.m4a"), 114.0, 150.0, copy_aac=False
    )

    assert command[command.index("-c:a") + 1] == "aac"
    assert command[command.index("-b:a") + 1] == "192k"


def test_exact_audio_extract_reencodes_114_to_150_with_reset_timestamps() -> None:
    command = build_exact_audio_extract_command(
        _tools(), Path("input.mp4"), Path("audio.m4a"), 114.0, 150.0
    )

    assert command[command.index("-ss") + 1] == "114.000000"
    assert command[command.index("-t") + 1] == "36.000000"
    assert command[command.index("-af") + 1] == ("atrim=duration=36.000000,asetpts=PTS-STARTPTS")
    assert command[command.index("-c:a") + 1] == "aac"


def test_mux_copies_video_and_audio_without_timing_filters() -> None:
    command = build_mux_command(_tools(), Path("visual.mp4"), Path("audio.m4a"), Path("final.mp4"))

    assert command[command.index("-c:v") + 1] == "copy"
    assert command[command.index("-c:a") + 1] == "copy"
    assert "-shortest" in command
    assert "GIF" not in " ".join(command).upper()


def test_duration_validation_reports_relevant_drift() -> None:
    timing = validate_timing(36.0, 36.02, 36.02)
    assert timing.difference_seconds == pytest.approx(0.02)

    with pytest.raises(CalendarAnimError, match="Audio/visual duration mismatch"):
        validate_timing(36.0, 36.2, 36.0)


def test_final_av_probe_accepts_audio_and_sync_within_50ms() -> None:
    probe = AVMediaProbe("h264", "aac", 1512, 864, 3.0, 108, 36.0, 36.02, 36.02, "1:1")

    validate_av_media(probe, (1512, 864))
    assert probe.av_delta_seconds == pytest.approx(0.02)


def test_final_av_probe_rejects_missing_or_out_of_sync_audio() -> None:
    with pytest.raises(CalendarAnimError, match="audio codec=none"):
        validate_av_media(
            AVMediaProbe("h264", "none", 1512, 864, 3.0, 108, 36.0, 36.0, 36.0, "1:1"),
            (1512, 864),
        )
    with pytest.raises(CalendarAnimError, match="A/V delta"):
        validate_av_media(
            AVMediaProbe("h264", "aac", 1512, 864, 3.0, 108, 36.0, 36.2, 36.0, "1:1"),
            (1512, 864),
        )
