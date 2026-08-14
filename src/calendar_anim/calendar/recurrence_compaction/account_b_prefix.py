import hashlib
import json
import os
import statistics
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, Field

from calendar_anim.calendar.multi_frame.artifacts import AnimationRunStore, initial_upload_state
from calendar_anim.calendar.multi_frame.models import FrameUploadPlan, MultiFramePlan
from calendar_anim.calendar.recurrence_compaction.hybrid import (
    FINAL_FRAME_INDEX,
    FINAL_HYBRID_RUN_ID,
    FINAL_INPUT_SHA256,
    FINAL_SOURCE_RUN_ID,
    _validate_source_invariants,
)
from calendar_anim.calendar.recurrence_compaction.models import (
    RecurrenceMigrationPlan,
    RecurrenceStudyReport,
)
from calendar_anim.calendar.recurrence_compaction.planner import build_recurrence_study
from calendar_anim.exceptions import CalendarAnimError

ACCOUNT_B_PREFIX_RUN_ID = "cayde-final-b-prefix-rdate-frames-001-023-01"
PREFIX_FIRST_FRAME_INDEX = 0
PREFIX_LAST_FRAME_INDEX = 22
EXISTING_B_FIRST_FRAME_INDEX = 23
PREFIX_FIRST_WEEK = date(2027, 10, 10)
PREFIX_LAST_WEEK = date(2028, 3, 12)
EXISTING_B_FIRST_WEEK = date(2028, 3, 19)
FINAL_WEEK = date(2029, 10, 28)
PREFIX_CHUNK_SIZE = 100


class PrefixSourceFrameStore(Protocol):
    def load_frame_plan(self, plan: MultiFramePlan, frame_index: int): ...  # type: ignore[no-untyped-def]


class _OriginalFrameAdapter:
    def __init__(self, store: AnimationRunStore, source_plan: MultiFramePlan) -> None:
        self.store = store
        self.source_plan = source_plan

    def load_frame_plan(self, _plan: MultiFramePlan, frame_index: int):  # type: ignore[no-untyped-def]
        return self.store.load_frame_plan(self.source_plan, frame_index)


class AccountBPrefixFinalReport(BaseModel):
    schema_version: str = "1.0"
    run_id: str
    source_run_id: str
    existing_b_run_id: str
    input_sha256: str
    calendar_profile: str = "account-b"
    calendar_name: str = "Calendar Animation Lab B"
    segment: str = "prefix"
    human_frames: str = "1-23"
    frame_indices: list[int]
    prefix_first_week: date
    prefix_last_week: date
    existing_b_first_week: date
    existing_b_last_week: date
    final_frame_week: date
    frame_23_to_24_delta_days: int
    all_week_deltas_seven_days: bool
    prefix_existing_week_overlap: int
    existing_b_parents: int
    existing_b_occurrences: int
    existing_b_plan_sha256: str
    existing_b_touched: bool = False
    logical_occurrences: int = Field(ge=0)
    unique_recurrence_signatures: int = Field(ge=0)
    recurring_parents: int = Field(ge=0)
    reduction_percent: float = Field(ge=0, le=100)
    singleton_parents: int = Field(ge=0)
    largest_group: int = Field(ge=0)
    largest_chunk: int = Field(ge=0)
    largest_rdate_count: int = Field(ge=0)
    payload_min_bytes: int = Field(ge=0)
    payload_mean_bytes: float = Field(ge=0)
    payload_p95_bytes: int = Field(ge=0)
    payload_max_bytes: int = Field(ge=0)
    parent_id_collisions: int = Field(ge=0)
    expansion_missing: int = Field(ge=0)
    expansion_extra: int = Field(ge=0)
    expansion_duplicates: int = Field(ge=0)
    expansion_exact: bool
    deterministic_parent_ids: bool
    eta_seconds: dict[str, float]
    account_a_touched: bool = False
    google_calendar_reads: bool = False
    google_calendar_writes: bool = False


def build_account_b_prefix_plan(
    source: MultiFramePlan, run_id: str = ACCOUNT_B_PREFIX_RUN_ID
) -> MultiFramePlan:
    _validate_source_invariants(source)
    selected = [
        frame
        for frame in source.frames
        if PREFIX_FIRST_FRAME_INDEX <= frame.frame_index <= PREFIX_LAST_FRAME_INDEX
    ]
    if [frame.frame_index for frame in selected] != list(range(23)):
        raise CalendarAnimError("Prefix source must contain exactly frame indices 0-22")
    frames = [
        FrameUploadPlan(
            **frame.model_dump(exclude={"frame_run_id", "calendar_profile", "calendar_id"}),
            frame_run_id=f"{run_id}-frame-{frame.frame_index:04d}",
            calendar_profile="account-b",
        )
        for frame in selected
    ]
    values = source.model_dump()
    values.update(
        {
            "run_id": run_id,
            "calendar_name": "Calendar Animation Lab B",
            "calendar_profile": "account-b",
            "start_week": frames[0].week_start,
            "frame_start": 0,
            "frame_count": len(frames),
            "events_per_frame": [frame.planned_events for frame in frames],
            "total_events": sum(frame.planned_events for frame in frames),
            "frames": frames,
        }
    )
    plan = MultiFramePlan.model_validate(values)
    _validate_prefix_weeks(plan, source)
    return plan


def build_account_b_prefix_artifacts(
    source_store: AnimationRunStore,
    existing_b_plan: RecurrenceMigrationPlan,
    *,
    source_run_id: str = FINAL_SOURCE_RUN_ID,
    run_id: str = ACCOUNT_B_PREFIX_RUN_ID,
    chunk_size: int = PREFIX_CHUNK_SIZE,
    existing_b_plan_sha256: str,
    generated_at: datetime | None = None,
) -> tuple[
    MultiFramePlan,
    RecurrenceMigrationPlan,
    RecurrenceStudyReport,
    AccountBPrefixFinalReport,
]:
    source = source_store.load_plan(source_run_id)
    prefix = build_account_b_prefix_plan(source, run_id)
    result = build_recurrence_study(
        prefix,
        initial_upload_state(prefix),
        _OriginalFrameAdapter(source_store, source),  # type: ignore[arg-type]
        migration_chunk_size=chunk_size,
        generated_at=generated_at or datetime.now(UTC),
    )
    recurrence = _with_prefix_metadata(result.migration_plan)
    payloads_after_metadata = [
        parent.estimated_insert_payload_bytes for parent in recurrence.parents
    ]
    study = result.report.model_copy(
        update={
            "largest_migration_payload_bytes": max(payloads_after_metadata, default=0),
            "mean_migration_payload_bytes": (
                statistics.fmean(payloads_after_metadata) if payloads_after_metadata else 0.0
            ),
        }
    )
    expected_keys = {key for parent in recurrence.parents for key in parent.occurrence_keys}
    expanded_keys = [key for parent in recurrence.parents for key in parent.occurrence_keys]
    duplicates = len(expanded_keys) - len(expected_keys)
    missing = max(0, prefix.total_events - len(expected_keys))
    extra = max(0, len(expected_keys) - prefix.total_events)
    existing_ids = {parent.parent_id for parent in existing_b_plan.parents}
    prefix_ids = {parent.parent_id for parent in recurrence.parents}
    collisions = len(existing_ids & prefix_ids)
    prefix_weeks = {frame.week_start for frame in prefix.frames}
    existing_weeks = {frame.week_start for frame in source.frames[23:]}
    overlap = len(prefix_weeks & existing_weeks)
    payloads = sorted(parent.estimated_insert_payload_bytes for parent in recurrence.parents)
    if not payloads:
        raise CalendarAnimError("Prefix recurrence plan contains no parents")
    p95 = payloads[max(0, (95 * len(payloads) + 99) // 100 - 1)]
    weeks = [frame.week_start for frame in source.frames]
    week_deltas = [(right - left).days for left, right in zip(weeks, weeks[1:], strict=False)]
    report = AccountBPrefixFinalReport(
        run_id=run_id,
        source_run_id=source_run_id,
        existing_b_run_id=FINAL_HYBRID_RUN_ID,
        input_sha256=FINAL_INPUT_SHA256,
        frame_indices=list(range(23)),
        prefix_first_week=prefix.frames[0].week_start,
        prefix_last_week=prefix.frames[-1].week_start,
        existing_b_first_week=source.frames[23].week_start,
        existing_b_last_week=source.frames[-1].week_start,
        final_frame_week=source.frames[FINAL_FRAME_INDEX].week_start,
        frame_23_to_24_delta_days=(
            source.frames[23].week_start - prefix.frames[-1].week_start
        ).days,
        all_week_deltas_seven_days=all(delta == 7 for delta in week_deltas),
        prefix_existing_week_overlap=overlap,
        existing_b_parents=len(existing_b_plan.parents),
        existing_b_occurrences=existing_b_plan.expanded_occurrence_count,
        existing_b_plan_sha256=existing_b_plan_sha256,
        logical_occurrences=prefix.total_events,
        unique_recurrence_signatures=study.unique_exact_signatures,
        recurring_parents=len(recurrence.parents),
        reduction_percent=study.migration_insert_reduction,
        singleton_parents=sum(parent.occurrence_count == 1 for parent in recurrence.parents),
        largest_group=study.distribution.largest,
        largest_chunk=max(parent.occurrence_count for parent in recurrence.parents),
        largest_rdate_count=max(parent.occurrence_count - 1 for parent in recurrence.parents),
        payload_min_bytes=payloads[0],
        payload_mean_bytes=statistics.fmean(payloads),
        payload_p95_bytes=p95,
        payload_max_bytes=payloads[-1],
        parent_id_collisions=collisions,
        expansion_missing=missing,
        expansion_extra=extra,
        expansion_duplicates=duplicates,
        expansion_exact=(
            recurrence.expansion_equals_missing
            and duplicates == missing == extra == 0
            and len(expanded_keys) == prefix.total_events
        ),
        deterministic_parent_ids=(
            len(prefix_ids) == len(recurrence.parents)
            and all(
                parent.private_metadata.get("run_id") == run_id for parent in recurrence.parents
            )
        ),
        eta_seconds={
            str(interval): len(recurrence.parents) * interval for interval in (0.75, 1.0, 1.5, 2.0)
        },
    )
    validate_account_b_prefix_report(report)
    return prefix, recurrence, study, report


def validate_account_b_prefix_report(report: AccountBPrefixFinalReport) -> None:
    failures = {
        "prefix frame sequence": report.frame_indices != list(range(23)),
        "frame 1 week": report.prefix_first_week != PREFIX_FIRST_WEEK,
        "frame 23 week": report.prefix_last_week != PREFIX_LAST_WEEK,
        "frame 24 week": report.existing_b_first_week != EXISTING_B_FIRST_WEEK,
        "frame 108 week": report.final_frame_week != FINAL_WEEK,
        "frame 23 -> 24 continuity": report.frame_23_to_24_delta_days != 7,
        "108-week continuity": not report.all_week_deltas_seven_days,
        "prefix/existing week overlap": report.prefix_existing_week_overlap != 0,
        "prefix/existing parent ID collision": report.parent_id_collisions != 0,
        "prefix expansion equality": not report.expansion_exact,
        "existing B parent count": report.existing_b_parents != 32021,
        "existing B occurrence count": report.existing_b_occurrences != 214596,
    }
    failed = [name for name, value in failures.items() if value]
    if failed:
        raise CalendarAnimError("Account-B prefix safety gate failed: " + ", ".join(failed))


def validate_prefix_input_hash(path: Path) -> None:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as error:
        raise CalendarAnimError(f"Could not hash final source input: {path}") from error
    if digest.hexdigest() != FINAL_INPUT_SHA256:
        raise CalendarAnimError("input.mp4 hash differs from the approved final source")


def _with_prefix_metadata(plan: RecurrenceMigrationPlan) -> RecurrenceMigrationPlan:
    parents = []
    for parent in plan.parents:
        metadata = {
            **parent.private_metadata,
            "segment": "prefix",
            "human_frames": "1-23",
        }
        signature = parent.signature
        payload: dict[str, object] = {
            "id": parent.parent_id,
            "summary": signature.summary,
            "start": {
                "dateTime": parent.start.isoformat(),
                "timeZone": signature.timezone,
            },
            "end": {
                "dateTime": parent.end.isoformat(),
                "timeZone": signature.timezone,
            },
            "transparency": signature.transparency,
            "visibility": signature.visibility,
            "eventType": signature.event_type,
            "extendedProperties": {"private": metadata},
        }
        if signature.color_id:
            payload["colorId"] = signature.color_id
        if parent.recurrence:
            payload["recurrence"] = parent.recurrence
        parents.append(
            parent.model_copy(
                update={
                    "private_metadata": metadata,
                    "estimated_insert_payload_bytes": len(
                        json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode(
                            "utf-8"
                        )
                    ),
                }
            )
        )
    return plan.model_copy(update={"parents": parents})


def save_account_b_prefix_artifacts(
    directory: Path,
    animation: MultiFramePlan,
    recurrence: RecurrenceMigrationPlan,
    recurrence_report: RecurrenceStudyReport,
    final_report: AccountBPrefixFinalReport,
) -> list[Path]:
    payloads = {
        "prefix-animation-plan.json": animation.model_dump_json(indent=2) + "\n",
        "prefix-recurrence-plan.json": recurrence.model_dump_json(indent=2) + "\n",
        "prefix-recurrence-report.json": recurrence_report.model_dump_json(indent=2) + "\n",
        "prefix-final-report.json": final_report.model_dump_json(indent=2) + "\n",
        "prefix-final-report.txt": build_account_b_prefix_text(final_report),
    }
    return [_write_atomic(directory / name, payload) for name, payload in payloads.items()]


def build_account_b_prefix_text(report: AccountBPrefixFinalReport) -> str:
    def duration(seconds: float) -> str:
        rounded = round(seconds)
        hours, remainder = divmod(rounded, 3600)
        minutes, seconds = divmod(remainder, 60)
        return f"{hours}h {minutes:02d}m {seconds:02d}s"

    return "\n".join(
        [
            "ACCOUNT-B FULL ANIMATION PREFIX PLAN",
            "====================================",
            "",
            "Existing B",
            "----------",
            "Frames: human 24-108 (indices 23-107)",
            f"Weeks: {report.existing_b_first_week} -> {report.existing_b_last_week}",
            f"Parents: {report.existing_b_parents}",
            f"Occurrences: {report.existing_b_occurrences}",
            "Touched: NO",
            "",
            "New prefix B",
            "------------",
            "Frames: human 1-23 (indices 0-22)",
            f"Weeks: {report.prefix_first_week} -> {report.prefix_last_week}",
            f"Logical occurrences: {report.logical_occurrences}",
            f"Unique signatures: {report.unique_recurrence_signatures}",
            f"Parents chunk100: {report.recurring_parents}",
            f"Reduction: {report.reduction_percent:.3f}%",
            f"Singleton parents: {report.singleton_parents}",
            f"Largest group: {report.largest_group}",
            f"Largest chunk: {report.largest_chunk}",
            f"Largest RDATE count: {report.largest_rdate_count}",
            "",
            "Continuity",
            "----------",
            f"Frame 1: {report.prefix_first_week}",
            f"Frame 23: {report.prefix_last_week}",
            f"Frame 24: {report.existing_b_first_week}",
            f"Frame 108: {report.final_frame_week}",
            f"Frame23->24 delta: {report.frame_23_to_24_delta_days} days",
            f"Overlap: {report.prefix_existing_week_overlap}",
            f"Parent ID collisions: {report.parent_id_collisions}",
            "",
            "Expansion",
            "---------",
            f"Missing: {report.expansion_missing}",
            f"Extra: {report.expansion_extra}",
            f"Duplicates: {report.expansion_duplicates}",
            f"Exact equality: {'YES' if report.expansion_exact else 'NO'}",
            "",
            "Payload",
            "-------",
            f"Min: {report.payload_min_bytes} bytes",
            f"Mean: {report.payload_mean_bytes:.1f} bytes",
            f"p95: {report.payload_p95_bytes} bytes",
            f"Max: {report.payload_max_bytes} bytes",
            "",
            "ETA",
            "---",
            *[
                f"@{interval}: {duration(report.eta_seconds[interval])}"
                for interval in ("0.75", "1.0", "1.5", "2.0")
            ],
            "",
            "Account A",
            "---------",
            "Touched: NO",
            "",
            "Calendar reads: NO",
            "Calendar writes: NO",
            "",
        ]
    )


def _validate_prefix_weeks(prefix: MultiFramePlan, source: MultiFramePlan) -> None:
    if prefix.frames[0].week_start != PREFIX_FIRST_WEEK:
        raise CalendarAnimError("Prefix frame 1 must map to 2027-10-10")
    if prefix.frames[-1].week_start != PREFIX_LAST_WEEK:
        raise CalendarAnimError("Prefix frame 23 must map to 2028-03-12")
    if source.frames[23].week_start != EXISTING_B_FIRST_WEEK:
        raise CalendarAnimError("Existing B frame 24 must map to 2028-03-19")
    if (source.frames[23].week_start - prefix.frames[-1].week_start) != timedelta(days=7):
        raise CalendarAnimError("Frame 23 -> frame 24 must be exactly seven days")


def _write_atomic(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()
    return path
