import json
from collections.abc import Callable
from contextlib import AbstractContextManager
from pathlib import Path
from typing import Annotated, Never

import typer
from PIL import Image, ImageDraw

from calendar_anim.browser.playwright_gateway import PlaywrightCalendarCaptureGateway
from calendar_anim.calendar.capture.models import BrowserChannel, CalendarCaptureConfig
from calendar_anim.calendar.cayde_216.artifacts import Cayde216Store, write_atomic
from calendar_anim.calendar.cayde_216.planner import (
    EXPECTED_INPUT_SHA256,
    FIRST_WEEK,
    FPS,
    FRAME_COUNT,
    RUN_ID,
    SOURCE_RUN_ID,
)
from calendar_anim.calendar.cayde_216.upload import (
    upload_store,
    validate_cayde_216_upload,
)
from calendar_anim.calendar.hybrid_capture.artifacts import (
    AccountBSingleCaptureStore,
    parse_output_resolution,
    resolution_name,
)
from calendar_anim.calendar.hybrid_capture.models import (
    HybridCapturePlan,
    HybridFramePlan,
    HybridOutputMode,
    SingleProfilePreviewReport,
)
from calendar_anim.calendar.hybrid_capture.service import (
    HybridBrowserGateway,
    HybridCaptureService,
)
from calendar_anim.calendar.profiles.store import CalendarProfileStore
from calendar_anim.exceptions import CalendarAnimError

PREVIEW_HUMAN_FRAMES = (1, 54, 108, 162, 216)
PREVIEW_FRAMES_TEXT = ",".join(str(frame) for frame in PREVIEW_HUMAN_FRAMES)
CAPTURE_RESOLUTION = (1512, 864)
CAYDE_216_STABILIZATION_SECONDS = 5.0


def _fail(error: Exception) -> Never:
    typer.secho(f"Error: {error}", fg=typer.colors.RED, err=True)
    raise typer.Exit(code=1)


def _gateway_factory(
    stabilization_seconds: float, ready_timeout_seconds: float
) -> Callable[[str, int], AbstractContextManager[HybridBrowserGateway]]:
    profiles = CalendarProfileStore()

    def factory(
        profile_name: str, expected_zoom: int
    ) -> AbstractContextManager[HybridBrowserGateway]:
        profile = profiles.load(profile_name)
        if profile.capture_zoom_percent != expected_zoom:
            raise CalendarAnimError(
                f"Profile {profile_name} zoom is {profile.capture_zoom_percent}%, "
                f"expected {expected_zoom}%"
            )
        config = CalendarCaptureConfig(
            profile_directory=profile.browser_profile_directory,
            profile_name=profile.profile_name,
            expected_google_account=profile.authenticated_google_account,
            expected_calendar_name=profile.calendar_name,
            browser_zoom_percent=profile.capture_zoom_percent,
            visible_start_hour=6,
            visible_end_hour=24,
            stabilization_seconds=stabilization_seconds,
            ready_timeout_seconds=ready_timeout_seconds,
            browser_channel=BrowserChannel.CHROME,
        )
        return PlaywrightCalendarCaptureGateway(config)  # type: ignore[return-value]

    return factory


def build_cayde_216_capture_plan(
    store: Cayde216Store | None = None,
) -> HybridCapturePlan:
    source_store = store or Cayde216Store()
    source = source_store.load_plan(RUN_ID)
    if (
        source.frame_count != FRAME_COUNT
        or [frame.frame_index for frame in source.frames] != list(range(FRAME_COUNT))
        or source.output_fps != FPS
        or source.target_grid_width != 126
        or source.target_grid_height != 72
        or source.palette_preset != "cayde-cyan-magenta"
        or source.clip_start_seconds != 114.0
        or source.clip_end_seconds != 150.0
        or source.start_week != FIRST_WEEK
        or source.calendar_profile != "account-b"
    ):
        raise CalendarAnimError("Cayde 216 capture source invariants changed")
    return HybridCapturePlan(
        schema_version="3.0",
        capture_strategy="single-profile-account-b",
        run_id=RUN_ID,
        source_run_id=SOURCE_RUN_ID,
        source_sha256=EXPECTED_INPUT_SHA256,
        frame_count=FRAME_COUNT,
        fps=FPS,
        grid_width=126,
        grid_height=72,
        normalized_width=CAPTURE_RESOLUTION[0],
        normalized_height=CAPTURE_RESOLUTION[1],
        clip_start_seconds=114.0,
        clip_end_seconds=150.0,
        frames=[
            HybridFramePlan(
                frame_index=frame.frame_index,
                human_frame=frame.frame_index + 1,
                week_start=frame.week_start,
                calendar_profile="account-b",
                calendar_name="Calendar Animation Lab B",
                capture_zoom_percent=90,
                expected_occurrences=frame.planned_events,
                source_frame_plan=str(
                    source_store.frame_directory(source, frame.frame_index) / "frame-plan.json"
                ),
            )
            for frame in source.frames
        ],
    )


def capture_cayde_216_preview_command(
    run_id: Annotated[str, typer.Option("--run-id")] = RUN_ID,
    profile: Annotated[str, typer.Option("--profile")] = "account-b",
    frames: Annotated[str, typer.Option("--frames")] = PREVIEW_FRAMES_TEXT,
    mode: Annotated[HybridOutputMode, typer.Option("--mode")] = (
        HybridOutputMode.HEADER_PRESERVED_FILL
    ),
    resolution: Annotated[str, typer.Option("--resolution")] = "1512x864",
    stabilization_seconds: Annotated[
        float, typer.Option("--stabilization-seconds", min=0)
    ] = CAYDE_216_STABILIZATION_SECONDS,
    ready_timeout_seconds: Annotated[float, typer.Option("--ready-timeout-seconds", min=1)] = 90,
    execute: Annotated[bool, typer.Option("--execute")] = False,
) -> None:
    """Capture the five approved 216-frame sanity samples through Account B read-only."""

    try:
        if run_id != RUN_ID or profile != "account-b":
            raise CalendarAnimError("Cayde 216 preview is locked to its final run and account-b")
        selected = _parse_preview_frames(frames)
        if mode is not HybridOutputMode.HEADER_PRESERVED_FILL:
            raise CalendarAnimError("Cayde 216 preview requires header_preserved_fill")
        output_resolution = parse_output_resolution(resolution)
        if output_resolution != CAPTURE_RESOLUTION:
            raise CalendarAnimError("Cayde 216 preview requires resolution 1512x864")
        validate_cayde_216_upload(run_id, Path("input.mp4"))
        state = upload_store().load_state(run_id)
        if state.completed_count != len(state.parents) or len(state.parents) != 43_781:
            raise CalendarAnimError("Cayde 216 bulk upload is not complete at 43781/43781")
        plan = build_cayde_216_capture_plan()
        store = AccountBSingleCaptureStore(Path("output/216-runs"))
        store.save_plan(plan)
    except (CalendarAnimError, OSError, RuntimeError, ValueError) as error:
        _fail(error)
    typer.echo("CAYDE 216 FINAL CAPTURE PREVIEW")
    typer.echo(f"Run: {run_id}")
    typer.echo("Profile: account-b only")
    typer.echo("Zoom: 90%")
    typer.echo(f"Frames: {', '.join(str(frame) for frame in selected)}")
    for human_frame in selected:
        frame = plan.frames[human_frame - 1]
        typer.echo(f"Frame {human_frame}: index {frame.frame_index}, week {frame.week_start}")
    typer.echo(f"Mode: {mode.value}")
    typer.echo(f"Resolution: {resolution_name(output_resolution)}")
    typer.echo(f"Execution: {'READ-ONLY BROWSER' if execute else 'DRY RUN'}")
    typer.echo(f"Output: {store.preview_directory(run_id)}")
    if not execute:
        typer.echo("Full capture checkpoint: NOT TOUCHED")
        typer.echo("Google Calendar writes: NO")
        return
    try:
        report = HybridCaptureService(
            store, _gateway_factory(stabilization_seconds, ready_timeout_seconds)
        ).capture_final_single_profile_preview(
            plan,
            selected,
            mode,
            output_resolution,
            fresh_session_per_frame=True,
            minimum_event_count=1,
        )
        contact_sheet = _build_contact_sheet(store, report)
        _record_contact_sheet(store, report, contact_sheet)
    except KeyboardInterrupt:
        typer.secho("Preview interrupted; full capture checkpoint was untouched.", fg="yellow")
        raise typer.Exit(code=130) from None
    except (CalendarAnimError, OSError, RuntimeError, ValueError) as error:
        _fail(error)
    for result in report.frames:
        typer.echo(
            f"Frame {result.human_frame}: week={result.expected_week}, "
            f"capture={result.capture}, output={result.output}"
        )
    typer.echo(f"Geometry consistent: {'YES' if report.geometry_consistent else 'NO'}")
    if report.geometry_warning:
        typer.secho(f"WARNING: {report.geometry_warning}", fg="yellow")
    typer.echo(f"Contact sheet: {contact_sheet}")
    typer.echo(f"Report: {store.preview_report_path(run_id)}")
    typer.echo("Full capture checkpoint: NOT TOUCHED")
    typer.echo("Google Calendar writes: NO")


def _parse_preview_frames(value: str) -> list[int]:
    try:
        selected = [int(item.strip()) for item in value.split(",") if item.strip()]
    except ValueError as error:
        raise CalendarAnimError("--frames must be comma-separated human frame numbers") from error
    if tuple(selected) != PREVIEW_HUMAN_FRAMES:
        raise CalendarAnimError(f"Cayde 216 sanity frames must be {PREVIEW_FRAMES_TEXT}")
    return selected


def _build_contact_sheet(
    store: AccountBSingleCaptureStore, report: SingleProfilePreviewReport
) -> Path:
    tile_size = (756, 432)
    label_height = 36
    sheet = Image.new("RGB", (tile_size[0] * 3, (tile_size[1] + label_height) * 2), "#101214")
    draw = ImageDraw.Draw(sheet)
    for position, frame in enumerate(report.frames):
        with Image.open(frame.output) as source:
            tile = source.convert("RGB").resize(tile_size, Image.Resampling.LANCZOS)
        column = position % 3
        row = position // 3
        x = column * tile_size[0]
        y = row * (tile_size[1] + label_height)
        draw.text(
            (x + 12, y + 11),
            f"FRAME {frame.human_frame} — {frame.expected_week}",
            fill="white",
        )
        sheet.paste(tile, (x, y + label_height))
    output = store.preview_directory(report.run_id) / "comparison-contact-sheet.png"
    output.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(output)
    return output


def _record_contact_sheet(
    store: AccountBSingleCaptureStore,
    report: SingleProfilePreviewReport,
    contact_sheet: Path,
) -> None:
    report_path = store.preview_report_path(report.run_id)
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    payload["contact_sheet"] = str(contact_sheet)
    write_atomic(report_path, json.dumps(payload, indent=2) + "\n")
    text_path = store.preview_report_text_path(report.run_id)
    current = text_path.read_text(encoding="utf-8")
    write_atomic(text_path, current + f"Contact sheet: {contact_sheet}\n")
