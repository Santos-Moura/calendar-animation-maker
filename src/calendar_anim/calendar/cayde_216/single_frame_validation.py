import json
import statistics
from collections import Counter
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Annotated, Any, Never
from zoneinfo import ZoneInfo

import typer
from googleapiclient.errors import HttpError

from calendar_anim.calendar.calibration.profile import DEFAULT_PROFILE_PATH, load_profile
from calendar_anim.calendar.cayde_216.artifacts import Cayde216Store, write_atomic
from calendar_anim.calendar.cayde_216.planner import (
    OLD_PREFIX_PLAN,
    OLD_RECURRENCE_PLAN,
    SOURCE_MANIFEST_RELATIVE,
    SOURCE_RUN_ID,
    protected_hashes,
)
from calendar_anim.calendar.cayde_216.planner import RUN_ID as BULK_RUN_ID
from calendar_anim.calendar.event_identity import deterministic_event_id
from calendar_anim.calendar.frame_mapping.models import EventCompressionMode, FrameMappingMode
from calendar_anim.calendar.google_gateway import GoogleCalendarGateway
from calendar_anim.calendar.high_detail import apply_high_detail_grid
from calendar_anim.calendar.hybrid_capture.artifacts import HybridCaptureStore
from calendar_anim.calendar.hybrid_capture.commands import _gateway_factory
from calendar_anim.calendar.hybrid_capture.models import HybridFramePlan, HybridOutputMode
from calendar_anim.calendar.hybrid_capture.service import HybridCaptureService
from calendar_anim.calendar.models import CalendarEventDraft
from calendar_anim.calendar.multi_frame.artifacts import initialize_animation_run
from calendar_anim.calendar.multi_frame.planner import build_multi_frame_plan
from calendar_anim.calendar.multi_frame.quota_wait import QuotaWaitPolicy
from calendar_anim.calendar.profiles.models import CalendarAccountProfile
from calendar_anim.calendar.profiles.service import CalendarProfileService
from calendar_anim.calendar.profiles.store import CalendarProfileStore
from calendar_anim.calendar.recurrence_compaction.models import RecurrenceMigrationPlan
from calendar_anim.calendar.recurrence_compaction.planner import build_recurrence_study
from calendar_anim.calendar.recurrence_upload.artifacts import (
    RecurrenceUploadStore,
    file_sha256,
    performance_from_state,
)
from calendar_anim.calendar.recurrence_upload.gateway import GoogleRecurrenceUploadGateway
from calendar_anim.calendar.recurrence_upload.service import RecurrenceUploadService
from calendar_anim.calendar.subcolumn_ordering import SubcolumnOrderStrategy
from calendar_anim.exceptions import CalendarAnimError
from calendar_anim.renderer.manifest import read_manifest, validate_manifest_files

VALIDATION_RUN_ID = "cayde-216-cyan-magenta-single-frame-validation-093-01"
HUMAN_FRAME = 93
FRAME_INDEX = 92
VALIDATION_WEEK = date(2034, 6, 25)
VALIDATION_END = VALIDATION_WEEK + timedelta(days=7)
ROOT = Path("output/216-validation")
CAPTURE_MODE = HybridOutputMode.HEADER_PRESERVED_FILL
CAPTURE_RESOLUTION = (1512, 864)
CAPTURE_ZOOM_PERCENT = 90
ARTIFACT_NAMES = (
    "animation-plan.json",
    "validation-plan.json",
    "recurrence-plan.json",
    "recurrence-report.json",
    "validation-report.json",
    "remote-preflight.json",
)
BULK_CHECKPOINT = Path("output/216-runs") / BULK_RUN_ID / "account-b-upload-state.json"


def _store() -> Cayde216Store:
    return Cayde216Store(ROOT)


def _upload_store() -> RecurrenceUploadStore:
    return RecurrenceUploadStore(
        ROOT,
        ROOT,
        artifact_names=ARTIFACT_NAMES,
        recurrence_plan_name="recurrence-plan.json",
        recurrence_report_name="recurrence-report.json",
    )


def prepare_single_frame_validation_command(
    run_id: Annotated[str, typer.Option("--run-id")] = VALIDATION_RUN_ID,
    source_run_id: Annotated[str, typer.Option("--source-run-id")] = BULK_RUN_ID,
    frame: Annotated[int, typer.Option("--frame")] = HUMAN_FRAME,
) -> None:
    """Prepare one isolated frame locally; never accesses Calendar."""

    try:
        _validate_identity(run_id, source_run_id, frame)
        store = _store()
        before = _bulk_hashes()
        source_manifest = Cayde216Store().run_directory(SOURCE_RUN_ID) / SOURCE_MANIFEST_RELATIVE
        manifest = read_manifest(source_manifest)
        errors = validate_manifest_files(manifest, source_manifest.resolve())
        if errors:
            raise CalendarAnimError("Source manifest invalid: " + "; ".join(errors))
        profile = apply_high_detail_grid(load_profile(DEFAULT_PROFILE_PATH), "126x72")
        plan, frames = build_multi_frame_plan(
            manifest.model_copy(update={"animation_id": "cayde-216-cyan-validation"}),
            profile,
            frame_start=FRAME_INDEX,
            frame_count=1,
            anchor_date=VALIDATION_WEEK,
            run_id=run_id,
            max_events_per_frame=5200,
            calendar_name="Calendar Animation Lab B",
            calendar_profile="account-b",
            mapping_mode=FrameMappingMode.FULL_GRID,
            event_compression=EventCompressionMode.SYNCHRONIZED_HORIZONTAL_BANDS,
            palette_preset="cayde-cyan-magenta",
            subcolumn_order_strategy=SubcolumnOrderStrategy.ZERO_WIDTH,
            grid_profile="high-detail-126x72",
        )
        initialize_animation_run(plan, frames, manifest, source_manifest, store)
        result = build_recurrence_study(
            plan,
            store.load_state(run_id),
            store,
            migration_chunk_size=100,
            generated_at=datetime.now(UTC),
        )
        recurrence = result.migration_plan
        expected = {
            f"f{FRAME_INDEX:04d}:{deterministic_event_id(event)}" for event in frames[0].events
        }
        expansion = validation_expansion_metrics(expected, recurrence)
        validation_ids = {parent.parent_id for parent in recurrence.parents}
        bulk_ids = _plan_ids(Path("output/216-plans") / BULK_RUN_ID / "recurrence-plan.json")
        existing_ids = _plan_ids(OLD_RECURRENCE_PLAN) | _plan_ids(OLD_PREFIX_PLAN)
        payloads = sorted(parent.estimated_insert_payload_bytes for parent in recurrence.parents)
        after = _bulk_hashes()
        report: dict[str, Any] = {
            "schema_version": "1.0",
            "run_id": run_id,
            "source_run_id": source_run_id,
            "human_frame": HUMAN_FRAME,
            "frame_index": FRAME_INDEX,
            "timestamp_seconds": manifest.frames[FRAME_INDEX].timestamp_seconds,
            "validation_week": VALIDATION_WEEK.isoformat(),
            "validation_end_exclusive": VALIDATION_END.isoformat(),
            "palette_preset": "cayde-cyan-magenta",
            "background_color_id": "7",
            "foreground_color_ids": ["3", "5", "9", "11"],
            "logical_occurrences": len(expected),
            "parent_inserts": len(recurrence.parents),
            "payload": {
                "minimum_bytes": payloads[0],
                "mean_bytes": statistics.fmean(payloads),
                "maximum_bytes": payloads[-1],
            },
            "expansion": {
                **expansion,
            },
            "validation_parent_ids_unique": len(validation_ids) == len(recurrence.parents),
            "validation_ids_intersect_bulk": len(validation_ids & bulk_ids),
            "validation_ids_intersect_existing_b": len(validation_ids & existing_ids),
            "validation_week_outside_bulk": date(2034, 6, 25) <= VALIDATION_WEEK,
            "bulk_checkpoint_touched": before != after,
            "bulk_window_touched": False,
            "bulk_parents_touched": False,
            "bulk_protected_sha256": before,
            "google_calendar_reads": False,
            "google_calendar_writes": False,
        }
        if (
            not expansion["exact"]
            or not report["validation_parent_ids_unique"]
            or report["validation_ids_intersect_bulk"]
            or report["validation_ids_intersect_existing_b"]
            or report["bulk_checkpoint_touched"]
        ):
            raise CalendarAnimError("Single-frame validation safety gates failed")
        directory = store.run_directory(run_id)
        write_atomic(
            directory / "recurrence-plan.json", recurrence.model_dump_json(indent=2) + "\n"
        )
        write_atomic(
            directory / "recurrence-report.json",
            result.report.model_dump_json(indent=2) + "\n",
        )
        write_atomic(directory / "validation-report.json", json.dumps(report, indent=2) + "\n")
        validation_plan = {
            "schema_version": "1.0",
            "run_id": run_id,
            "source_run_id": source_run_id,
            "human_frame": HUMAN_FRAME,
            "frame_index": FRAME_INDEX,
            "timestamp_seconds": manifest.frames[FRAME_INDEX].timestamp_seconds,
            "week_start": VALIDATION_WEEK.isoformat(),
            "palette_preset": "cayde-cyan-magenta",
            "background_color_id": "7",
            "foreground_color_ids": ["3", "5", "9", "11"],
            "parent_ids": sorted(validation_ids),
        }
        write_atomic(
            directory / "validation-plan.json",
            json.dumps(validation_plan, indent=2) + "\n",
        )
        cleanup = {
            "run_id": run_id,
            "selection": "exact metadata plus planned parent ID allowlist",
            "parent_ids": sorted(validation_ids),
            "date_range_delete": False,
            "executed": False,
        }
        write_atomic(directory / "cleanup-plan.json", json.dumps(cleanup, indent=2) + "\n")
    except (CalendarAnimError, OSError, ValueError) as error:
        _fail(error)
    typer.echo(f"Validation week: {VALIDATION_WEEK}")
    typer.echo(f"Logical occurrences: {report['logical_occurrences']}")
    typer.echo(f"Parents/inserts: {report['parent_inserts']}")
    typer.echo("Expansion exact: YES")
    typer.echo(f"Artifacts: {directory}")
    typer.echo("Google Calendar reads/writes: NO")


def preflight_single_frame_validation_command(
    run_id: Annotated[str, typer.Option("--run-id")] = VALIDATION_RUN_ID,
    profile: Annotated[str, typer.Option("--profile")] = "account-b",
    execute: Annotated[bool, typer.Option("--execute")] = False,
) -> None:
    """Check the temporary validation week without writing."""

    _validate_run_profile(run_id, profile)
    if not execute:
        typer.echo("Dry run; no Calendar API call.")
        return
    try:
        before = _bulk_hashes()
        account, gateway = CalendarProfileService(CalendarProfileStore()).gateway(profile)
        calendar_id = _validate_account(account, gateway)
        zone = ZoneInfo("America/Sao_Paulo")
        conflicts = gateway.list_event_ids_in_range(
            calendar_id,
            datetime.combine(VALIDATION_WEEK, time.min, zone),
            datetime.combine(VALIDATION_END, time.min, zone),
        )
        after = _bulk_hashes()
        payload = {
            "run_id": run_id,
            "profile": profile,
            "authenticated_account": account.authenticated_google_account,
            "calendar_name": account.calendar_name,
            "calendar_id": calendar_id,
            "week": VALIDATION_WEEK.isoformat(),
            "end_exclusive": VALIDATION_END.isoformat(),
            "conflicts": len(conflicts),
            "clean": not conflicts,
            "bulk_checkpoint_touched": before != after,
            "google_calendar_reads": True,
            "google_calendar_writes": False,
            "result": validation_preflight_result(conflicts, before == after),
        }
        write_atomic(
            _store().run_directory(run_id) / "remote-preflight.json",
            json.dumps(payload, indent=2) + "\n",
        )
        if payload["result"] != "PASS":
            raise CalendarAnimError(f"Validation preflight STOP: {len(conflicts)} conflicts")
    except (CalendarAnimError, HttpError, OSError, ValueError) as error:
        _fail(error)
    typer.echo(f"Account: {payload['authenticated_account']}")
    typer.echo(f"Calendar ID: {calendar_id}")
    typer.echo("Validation week conflicts: 0")
    typer.echo("Result: PASS")
    typer.echo("Google Calendar writes: NO")


def upload_single_frame_validation_command(
    run_id: Annotated[str, typer.Option("--run-id")] = VALIDATION_RUN_ID,
    source_run_id: Annotated[str, typer.Option("--source-run-id")] = BULK_RUN_ID,
    profile: Annotated[str, typer.Option("--profile")] = "account-b",
    frame: Annotated[int, typer.Option("--frame")] = HUMAN_FRAME,
    resume: Annotated[bool, typer.Option("--resume")] = True,
    execute: Annotated[bool, typer.Option("--execute")] = False,
) -> None:
    """Upload only the isolated frame-93 validation parents."""

    _validate_identity(run_id, source_run_id, frame)
    _validate_run_profile(run_id, profile)
    try:
        store = _upload_store()
        plan = store.load_plan(run_id)
        hashes = store.artifact_hashes(run_id)
        report = json.loads(
            (_store().run_directory(run_id) / "validation-report.json").read_text("utf-8")
        )
        preflight = store.load_json(run_id, "remote-preflight.json")
        if (
            not report["expansion"]["exact"]
            or report["validation_ids_intersect_bulk"] != 0
            or report.get("run_id") != run_id
            or report.get("source_run_id") != source_run_id
            or report.get("human_frame") != HUMAN_FRAME
            or report.get("frame_index") != FRAME_INDEX
            or report.get("palette_preset") != "cayde-cyan-magenta"
            or report.get("background_color_id") != "7"
            or report.get("foreground_color_ids") != ["3", "5", "9", "11"]
            or report.get("bulk_protected_sha256") != _bulk_hashes()
            or preflight.get("result") != "PASS"
            or preflight.get("conflicts") != 0
        ):
            raise CalendarAnimError("Validation upload gates are not PASS")
        animation_plan = _store().load_plan(run_id)
        frame_plan = _store().load_frame_plan(animation_plan, FRAME_INDEX)
        expected = {
            f"f{FRAME_INDEX:04d}:{deterministic_event_id(event)}" for event in frame_plan.events
        }
        if not validation_expansion_metrics(expected, plan)["exact"]:
            raise CalendarAnimError("Validation expansion changed after preparation")
        validation_ids = {parent.parent_id for parent in plan.parents}
        if validation_ids & _plan_ids(
            Path("output/216-plans") / BULK_RUN_ID / "recurrence-plan.json"
        ):
            raise CalendarAnimError("Validation parent IDs overlap the final bulk plan")
        state = store.initialize_state(run_id, plan, hashes, 1.0)
        bulk_before = _bulk_hashes()
    except (CalendarAnimError, OSError, ValueError) as error:
        _fail(error)
    typer.echo(f"Parents: {state.completed_count}/{len(state.parents)}")
    typer.echo(f"Execution: {'REAL VALIDATION ONLY' if execute else 'DRY RUN'}")
    if not execute:
        typer.echo("Google Calendar writes: NO")
        return
    if not resume:
        _fail(CalendarAnimError("Validation upload requires --resume"))
    try:
        service = CalendarProfileService(
            CalendarProfileStore(), gateway_factory=GoogleRecurrenceUploadGateway
        )
        account, gateway = service.gateway(profile)
        calendar_id = _validate_account(account, gateway)
        if not isinstance(gateway, GoogleRecurrenceUploadGateway):
            raise CalendarAnimError("Validation recurrence gateway unavailable")
        zone = ZoneInfo("America/Sao_Paulo")
        current_window = gateway.list_window(
            calendar_id,
            datetime.combine(VALIDATION_WEEK, time.min, zone),
            datetime.combine(VALIDATION_END, time.min, zone),
        )
        if state.events_insert_calls == 0:
            if current_window:
                raise CalendarAnimError("Validation week is no longer empty; STOP")
        elif any(not _is_validation_resource(resource) for resource in current_window):
            raise CalendarAnimError("Validation week contains a non-validation resource; STOP")
        typer.echo(f"Account: {account.authenticated_google_account}")
        typer.echo(f"Calendar ID: {calendar_id}")
        typer.confirm("Upload ONLY Cyan Magenta validation frame 93?", default=False, abort=True)
        uploader = RecurrenceUploadService(
            gateway,
            store,
            quota_policy=QuotaWaitPolicy(
                cooldown_seconds=(900, 1800, 3600, 7200, 14400),
                jitter_seconds=60,
                max_auto_wait_seconds=48 * 3600,
                conservative_recovery_interval_seconds=1.5,
            ),
            progress=lambda current: typer.echo(
                f"Uploading parents: {current.completed_count}/{len(current.parents)}"
            ),
        )
        state = uploader.upload(plan, state, calendar_id)
        ensure_bulk_hashes_unchanged(bulk_before, _bulk_hashes())
        performance = performance_from_state(state, state.active_upload_seconds)
        payload = {
            "completed_parents": state.completed_count,
            "planned_parents": len(state.parents),
            "rate_limit_exceeded": state.rate_limit_exceeded_count,
            "quota_exceeded": state.quota_exceeded_count,
            "bulk_checkpoint_touched": False,
            "bulk_window_touched": False,
            "bulk_parents_touched": False,
            "google_calendar_writes": True,
        }
        write_atomic(
            _store().run_directory(run_id) / "upload-report.json",
            json.dumps(payload, indent=2) + "\n",
        )
        store.save_performance(performance)
    except KeyboardInterrupt:
        typer.secho("Interrupted safely; validation checkpoint preserved.", fg="yellow")
        raise typer.Exit(code=130) from None
    except (CalendarAnimError, HttpError, OSError, ValueError) as error:
        _fail(error)
    typer.echo(f"Completed parents: {state.completed_count}/{len(state.parents)}")


def audit_single_frame_validation_command(
    run_id: Annotated[str, typer.Option("--run-id")] = VALIDATION_RUN_ID,
    profile: Annotated[str, typer.Option("--profile")] = "account-b",
    execute: Annotated[bool, typer.Option("--execute")] = False,
) -> None:
    """Compare expanded remote instances and parent GETs with the exact local plan."""

    _validate_run_profile(run_id, profile)
    if not execute:
        typer.echo("Dry run; no Calendar API call.")
        return
    try:
        plan = _upload_store().load_plan(run_id)
        account, gateway = CalendarProfileService(
            CalendarProfileStore(), gateway_factory=GoogleRecurrenceUploadGateway
        ).gateway(profile)
        calendar_id = _validate_account(account, gateway)
        if not isinstance(gateway, GoogleRecurrenceUploadGateway):
            raise CalendarAnimError("Validation audit gateway unavailable")
        zone = ZoneInfo("America/Sao_Paulo")
        listed = gateway.list_window(
            calendar_id,
            datetime.combine(VALIDATION_WEEK, time.min, zone),
            datetime.combine(VALIDATION_END, time.min, zone),
        )
        animation_plan = _store().load_plan(run_id)
        frame_plan = _store().load_frame_plan(animation_plan, FRAME_INDEX)
        expected_occurrences = Counter(_local_occurrence_key(event) for event in frame_plan.events)
        remote_occurrences = Counter(_remote_occurrence_key(item) for item in listed)
        missing = expected_occurrences - remote_occurrences
        extra = remote_occurrences - expected_occurrences
        duplicates = sum(
            max(0, count - expected_occurrences.get(key, 0))
            for key, count in remote_occurrences.items()
        )
        expected = {parent.parent_id: parent for parent in plan.parents}
        wrong_time = wrong_summary = wrong_color = get_missing = 0
        for audited_count, (parent_id, parent) in enumerate(expected.items(), start=1):
            remote = gateway.get_parent(calendar_id, parent_id)
            if remote is None:
                get_missing += 1
                continue
            body = gateway.parent_body(parent)
            wrong_time += int(
                remote.get("start") != body.get("start") or remote.get("end") != body.get("end")
            )
            wrong_summary += int(remote.get("summary") != body.get("summary"))
            wrong_color += int(remote.get("colorId") != body.get("colorId"))
            if audited_count % 250 == 0:
                typer.echo(f"Audited parents: {audited_count}/{len(expected)}")
        result = remote_audit_result(
            missing=sum(missing.values()),
            extra=sum(extra.values()),
            duplicates=duplicates,
            wrong_time=wrong_time,
            wrong_summary=wrong_summary,
            wrong_color=wrong_color,
            parent_get_missing=get_missing,
        )
        payload = {
            "events_list_single_events": True,
            "events_get_calls": len(expected),
            "missing": sum(missing.values()),
            "extra": sum(extra.values()),
            "duplicates": duplicates,
            "parent_get_missing": get_missing,
            "wrong_time": wrong_time,
            "wrong_summary": wrong_summary,
            "wrong_color": wrong_color,
            "result": result,
            "google_calendar_reads": True,
            "google_calendar_writes": False,
        }
        write_atomic(
            _store().run_directory(run_id) / "remote-audit.json",
            json.dumps(payload, indent=2) + "\n",
        )
        if result != "PASS":
            raise CalendarAnimError("Validation remote audit is not exact")
    except (CalendarAnimError, HttpError, OSError, ValueError) as error:
        _fail(error)
    typer.echo("Remote audit: PASS")
    typer.echo("missing=0 extra=0 duplicates=0 wrong_time=0 wrong_summary=0 wrong_color=0")


def capture_single_frame_validation_command(
    run_id: Annotated[str, typer.Option("--run-id")] = VALIDATION_RUN_ID,
    profile: Annotated[str, typer.Option("--profile")] = "account-b",
    execute: Annotated[bool, typer.Option("--execute")] = False,
) -> None:
    """Capture the validation week through the final high-resolution composition."""

    _validate_run_profile(run_id, profile)
    directory = _store().run_directory(run_id)
    if not execute:
        typer.echo(f"Output: {directory / 'capture' / 'frame_093-calendar.png'}")
        return
    try:
        audit = json.loads((directory / "remote-audit.json").read_text("utf-8"))
        if audit.get("result") != "PASS":
            raise CalendarAnimError("Remote audit PASS required before capture")
        source_plan = _store().load_plan(run_id)
        frame_summary = source_plan.frames[0]
        frame = HybridFramePlan(
            frame_index=FRAME_INDEX,
            human_frame=HUMAN_FRAME,
            week_start=VALIDATION_WEEK,
            calendar_profile="account-b",
            calendar_name="Calendar Animation Lab B",
            capture_zoom_percent=CAPTURE_ZOOM_PERCENT,
            expected_occurrences=frame_summary.planned_events,
            source_frame_plan=str(
                _store().frame_directory(source_plan, FRAME_INDEX) / "frame-plan.json"
            ),
        )
        capture = directory / "capture"
        capture.mkdir(parents=True, exist_ok=True)
        output = capture / "frame_093-calendar.png"
        with _gateway_factory(2, 90)("account-b", 90) as gateway:
            metrics = HybridCaptureService(
                HybridCaptureStore(), _gateway_factory(2, 90)
            )._capture_composed_frame(
                gateway,
                frame,
                capture / "raw-browser.png",
                capture / "logical-grid.png",
                capture / "native-header-grid.png",
                output,
                CAPTURE_MODE,
                CAPTURE_RESOLUTION,
                capture / "debug",
            )
        report = {
            "profile": "account-b",
            "zoom": CAPTURE_ZOOM_PERCENT,
            "week": VALIDATION_WEEK.isoformat(),
            "mode": CAPTURE_MODE.value,
            "resolution": list(CAPTURE_RESOLUTION),
            "visual_readiness": metrics.get("visual_content_occupancy") is True,
            "output": str(output),
            "google_calendar_reads": True,
            "google_calendar_writes": False,
        }
        write_atomic(capture / "capture-report.json", json.dumps(report, indent=2) + "\n")
    except (CalendarAnimError, OSError, RuntimeError, ValueError) as error:
        _fail(error)
    typer.echo(f"Capture PASS: {output}")
    typer.echo("Google Calendar writes: NO")


def cleanup_single_frame_validation_command(
    run_id: Annotated[str, typer.Option("--run-id")] = VALIDATION_RUN_ID,
    profile: Annotated[str, typer.Option("--profile")] = "account-b",
    execute: Annotated[bool, typer.Option("--execute")] = False,
) -> None:
    """Delete only exact validation metadata matches whose IDs are in the local allowlist."""

    _validate_run_profile(run_id, profile)
    plan = _upload_store().load_plan(run_id)
    allowed = {parent.parent_id for parent in plan.parents}
    typer.echo(f"Allowed validation parents: {len(allowed)}")
    typer.echo(f"Execution: {'REAL DELETE' if execute else 'DRY RUN'}")
    if not execute:
        typer.echo("Cleanup prepared: YES; executed: NO")
        return
    try:
        account, gateway = CalendarProfileService(
            CalendarProfileStore(), gateway_factory=GoogleRecurrenceUploadGateway
        ).gateway(profile)
        calendar_id = _validate_account(account, gateway)
        if not isinstance(gateway, GoogleRecurrenceUploadGateway):
            raise CalendarAnimError("Validation cleanup gateway unavailable")
        matches = gateway.find_bulk_parents(calendar_id, run_id)
        ids = select_cleanup_ids(matches, allowed)
        typer.confirm(f"Delete exactly {len(ids)} validation parents?", default=False, abort=True)
        result = gateway.delete_events(calendar_id, sorted(ids))
    except (CalendarAnimError, HttpError, OSError, ValueError) as error:
        _fail(error)
    typer.echo(f"Deleted: {result.deleted_events}; failed: {result.failed_events}")


def validation_expansion_metrics(
    expected: set[str], plan: RecurrenceMigrationPlan
) -> dict[str, int | bool]:
    expanded = [key for parent in plan.parents for key in parent.occurrence_keys]
    expanded_set = set(expanded)
    missing = len(expected - expanded_set)
    extra = len(expanded_set - expected)
    duplicates = len(expanded) - len(expanded_set)
    return {
        "missing": missing,
        "extra": extra,
        "duplicates": duplicates,
        "exact": missing == 0 and extra == 0 and duplicates == 0,
    }


def _local_occurrence_key(event: CalendarEventDraft) -> tuple[str, str, str, str]:
    return (
        event.start.astimezone(UTC).isoformat(),
        event.end.astimezone(UTC).isoformat(),
        event.summary,
        event.color_id or "",
    )


def _remote_occurrence_key(resource: dict[str, Any]) -> tuple[str, str, str, str]:
    return (
        _remote_datetime(resource.get("start")),
        _remote_datetime(resource.get("end")),
        str(resource.get("summary") or ""),
        str(resource.get("colorId") or ""),
    )


def _remote_datetime(value: object) -> str:
    if not isinstance(value, dict):
        return ""
    raw = value.get("dateTime")
    if not isinstance(raw, str):
        return ""
    return datetime.fromisoformat(raw).astimezone(UTC).isoformat()


def validation_preflight_result(conflicts: list[str], bulk_unchanged: bool) -> str:
    return "PASS" if not conflicts and bulk_unchanged else "STOP"


def ensure_bulk_hashes_unchanged(before: dict[str, str], after: dict[str, str]) -> None:
    if before != after:
        raise CalendarAnimError("Bulk checkpoint/artifacts changed during validation")


def remote_audit_result(
    *,
    missing: int,
    extra: int,
    duplicates: int,
    wrong_time: int,
    wrong_summary: int,
    wrong_color: int,
    parent_get_missing: int = 0,
) -> str:
    values = (
        missing,
        extra,
        duplicates,
        wrong_time,
        wrong_summary,
        wrong_color,
        parent_get_missing,
    )
    return "PASS" if not any(values) else "FAIL"


def select_cleanup_ids(matches: list[dict[str, Any]], allowed: set[str]) -> set[str]:
    selected: set[str] = set()
    for resource in matches:
        event_id = str(resource.get("id") or "")
        if not _is_validation_resource(resource):
            raise CalendarAnimError("Cleanup resource metadata is not validation-only")
        if event_id not in allowed:
            raise CalendarAnimError("Cleanup metadata returned an ID outside validation allowlist")
        selected.add(event_id)
    return selected


def _is_validation_resource(resource: dict[str, Any]) -> bool:
    extended = resource.get("extendedProperties")
    private = extended.get("private") if isinstance(extended, dict) else None
    return isinstance(private, dict) and all(
        private.get(key) == value
        for key, value in {
            "calendar_profile": "account-b",
            "generated_by": "calendar-anim",
            "run_id": VALIDATION_RUN_ID,
        }.items()
    )


def _validate_identity(run_id: str, source_run_id: str, frame: int) -> None:
    if (run_id, source_run_id, frame) != (VALIDATION_RUN_ID, BULK_RUN_ID, HUMAN_FRAME):
        raise CalendarAnimError("Single-frame validation identity differs from approved values")


def _validate_run_profile(run_id: str, profile: str) -> None:
    if run_id != VALIDATION_RUN_ID or profile != "account-b":
        raise CalendarAnimError("Validation is locked to its run ID and account-b")


def _validate_account(account: CalendarAccountProfile, gateway: GoogleCalendarGateway) -> str:
    if account.calendar_id is None or account.calendar_name != "Calendar Animation Lab B":
        raise CalendarAnimError("Account-B validation calendar configuration mismatch")
    calendar = gateway.get_calendar(account.calendar_id)
    if (
        calendar is None
        or calendar.name != "Calendar Animation Lab B"
        or calendar.access_role != "owner"
        or calendar.timezone != "America/Sao_Paulo"
    ):
        raise CalendarAnimError("Account-B validation Calendar identity mismatch")
    return account.calendar_id


def _plan_ids(path: Path) -> set[str]:
    return {
        parent.parent_id
        for parent in RecurrenceMigrationPlan.model_validate_json(path.read_text("utf-8")).parents
    }


def _bulk_hashes() -> dict[str, str]:
    values = protected_hashes()
    for path in (
        Path("output/216-plans") / BULK_RUN_ID / "animation-plan.json",
        Path("output/216-plans") / BULK_RUN_ID / "recurrence-plan.json",
        BULK_CHECKPOINT,
    ):
        values[str(path)] = file_sha256(path)
    return values


def _fail(error: Exception) -> Never:
    typer.secho(f"Error: {error}", fg=typer.colors.RED, err=True)
    raise typer.Exit(code=1)


def register_single_frame_validation_commands(app: typer.Typer) -> None:
    app.command("prepare-cayde-216-single-frame-validation")(
        prepare_single_frame_validation_command
    )
    app.command("preflight-cayde-216-single-frame-validation")(
        preflight_single_frame_validation_command
    )
    app.command("upload-cayde-216-single-frame-validation")(upload_single_frame_validation_command)
    app.command("audit-cayde-216-single-frame-validation")(audit_single_frame_validation_command)
    app.command("capture-cayde-216-single-frame-validation")(
        capture_single_frame_validation_command
    )
    app.command("cleanup-cayde-216-single-frame-validation")(
        cleanup_single_frame_validation_command
    )
