import hashlib
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, Field, model_validator

from calendar_anim.calendar.multi_frame.artifacts import AnimationRunStore, initial_upload_state
from calendar_anim.calendar.multi_frame.models import FrameUploadPlan, MultiFramePlan
from calendar_anim.calendar.recurrence_compaction.models import (
    RecurrenceMigrationPlan,
    RecurrenceStudyReport,
)
from calendar_anim.calendar.recurrence_compaction.planner import build_recurrence_study
from calendar_anim.calendar.recurrence_validation.ordering import OrderingCaptureResult
from calendar_anim.exceptions import CalendarAnimError

FINAL_SOURCE_RUN_ID = "cayde-final-126x72-3fps-36s-01"
FINAL_HYBRID_RUN_ID = "cayde-final-hybrid-rdate-126x72-3fps-36s-01"
FINAL_INPUT_SHA256 = "c5c94c0c1361bd0a42034f7e7419abb1aba6d2b13b1ae7af1ac44bd1e152b507"
ACCOUNT_A_LAST_FRAME_INDEX = 22
ACCOUNT_B_FIRST_FRAME_INDEX = 23
FINAL_FRAME_INDEX = 107


class HybridFrameAssignment(BaseModel):
    frame_index: int = Field(ge=0, le=107)
    calendar_profile: str
    capture_zoom_percent: int


class HybridCaptureSegment(BaseModel):
    first_frame_index: int
    last_frame_index: int
    calendar_profile: str
    calendar_name: str
    capture_zoom_percent: int
    browser_profile_strategy: str = "dedicated-profile"


class HybridFinalReport(BaseModel):
    schema_version: str = "1.0"
    run_id: str
    source_run_id: str
    input_sha256: str
    ordering_validation_id: str
    ordering_result: str
    frame_assignments: list[HybridFrameAssignment]
    capture_segments: list[HybridCaptureSegment]
    account_a_frame_indices: list[int]
    account_b_frame_indices: list[int]
    frame_24_entirely_account_b: bool
    logical_occurrences_b: int
    independent_equivalent_inserts_b: int
    unique_recurrence_signatures_b: int
    recurrence_chunk_size: int
    recurring_parents_b: int
    api_reduction_percent: float
    largest_group: int
    largest_chunk: int
    largest_estimated_payload_bytes: int
    final_normalized_width: int = 504
    final_normalized_height: int = 288
    normalization: str = "logical-grid crop; nearest-neighbor only if scaling is required"
    transition_validation: str = "future frame index 22 (A) vs frame index 23 (B)"
    deterministic_ids: bool = True
    existing_account_a_events_preserved: bool = True
    bulk_writes: bool = False

    @model_validator(mode="after")
    def validate_boundary(self) -> "HybridFinalReport":
        if self.account_a_frame_indices != list(range(23)):
            raise ValueError("Account A must contain exactly frame indices 0-22")
        if self.account_b_frame_indices != list(range(23, 108)):
            raise ValueError("Account B must contain exactly frame indices 23-107")
        if not self.frame_24_entirely_account_b:
            raise ValueError("human frame 24 must be entirely assigned to Account B")
        return self


class SourceFrameStore(Protocol):
    def load_frame_plan(self, plan: MultiFramePlan, frame_index: int): ...  # type: ignore[no-untyped-def]


class _OriginalFrameAdapter:
    def __init__(
        self,
        store: AnimationRunStore,
        source_plan: MultiFramePlan,
    ) -> None:
        self.store = store
        self.source_plan = source_plan

    def load_frame_plan(self, _plan: MultiFramePlan, frame_index: int):  # type: ignore[no-untyped-def]
        return self.store.load_frame_plan(self.source_plan, frame_index)


def frame_assignments() -> list[HybridFrameAssignment]:
    return [
        HybridFrameAssignment(
            frame_index=index,
            calendar_profile="account-a" if index <= ACCOUNT_A_LAST_FRAME_INDEX else "account-b",
            capture_zoom_percent=33 if index <= ACCOUNT_A_LAST_FRAME_INDEX else 90,
        )
        for index in range(FINAL_FRAME_INDEX + 1)
    ]


def build_account_b_plan(
    source: MultiFramePlan, run_id: str = FINAL_HYBRID_RUN_ID
) -> MultiFramePlan:
    _validate_source_invariants(source)
    selected = [
        frame for frame in source.frames if frame.frame_index >= ACCOUNT_B_FIRST_FRAME_INDEX
    ]
    frames = [
        FrameUploadPlan(
            **frame.model_dump(exclude={"frame_run_id", "calendar_profile", "calendar_id"}),
            frame_run_id=f"{run_id}-frame-{frame.frame_index:04d}",
            calendar_profile="account-b",
        )
        for frame in selected
    ]
    events = [frame.planned_events for frame in frames]
    values = source.model_dump()
    values.update(
        {
            "run_id": run_id,
            "calendar_name": "Calendar Animation Lab B",
            "calendar_profile": "account-b",
            "start_week": frames[0].week_start,
            "frame_start": ACCOUNT_B_FIRST_FRAME_INDEX,
            "frame_count": len(frames),
            "events_per_frame": events,
            "total_events": sum(events),
            "frames": frames,
        }
    )
    return MultiFramePlan.model_validate(values)


def build_hybrid_final_artifacts(
    source_store: AnimationRunStore,
    ordering_result: OrderingCaptureResult,
    *,
    source_run_id: str = FINAL_SOURCE_RUN_ID,
    run_id: str = FINAL_HYBRID_RUN_ID,
    chunk_size: int = 100,
) -> tuple[MultiFramePlan, RecurrenceMigrationPlan, RecurrenceStudyReport, HybridFinalReport]:
    if ordering_result.result != "PASS":
        raise CalendarAnimError(
            "Hybrid final plan is gated on RECURRENCE ZERO-WIDTH ORDERING = PASS"
        )
    source = source_store.load_plan(source_run_id)
    b_plan = build_account_b_plan(source, run_id)
    state = initial_upload_state(b_plan)
    recurrence = build_recurrence_study(
        b_plan,
        state,
        _OriginalFrameAdapter(source_store, source),  # type: ignore[arg-type]
        migration_chunk_size=chunk_size,
        generated_at=datetime.now(UTC),
    )
    report = recurrence.report
    parents = recurrence.migration_plan.parents
    final = HybridFinalReport(
        run_id=run_id,
        source_run_id=source_run_id,
        input_sha256=FINAL_INPUT_SHA256,
        ordering_validation_id=ordering_result.validation_id,
        ordering_result=ordering_result.result,
        frame_assignments=frame_assignments(),
        capture_segments=[
            HybridCaptureSegment(
                first_frame_index=0,
                last_frame_index=22,
                calendar_profile="account-a",
                calendar_name="Calendar Animation Lab",
                capture_zoom_percent=33,
            ),
            HybridCaptureSegment(
                first_frame_index=23,
                last_frame_index=107,
                calendar_profile="account-b",
                calendar_name="Calendar Animation Lab B",
                capture_zoom_percent=90,
            ),
        ],
        account_a_frame_indices=list(range(23)),
        account_b_frame_indices=list(range(23, 108)),
        frame_24_entirely_account_b=all(
            item.calendar_profile == "account-b"
            for item in frame_assignments()
            if item.frame_index == 23
        ),
        logical_occurrences_b=report.rendered_instances,
        independent_equivalent_inserts_b=report.current_independent_inserts,
        unique_recurrence_signatures_b=report.unique_exact_signatures,
        recurrence_chunk_size=chunk_size,
        recurring_parents_b=len(parents),
        api_reduction_percent=report.migration_insert_reduction,
        largest_group=report.distribution.largest,
        largest_chunk=max((parent.occurrence_count for parent in parents), default=0),
        largest_estimated_payload_bytes=report.largest_migration_payload_bytes,
    )
    return b_plan, recurrence.migration_plan, report, final


def validate_input_hash(path: Path) -> None:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as error:
        raise CalendarAnimError(f"Could not hash final source input: {path}") from error
    if digest.hexdigest() != FINAL_INPUT_SHA256:
        raise CalendarAnimError("input.mp4 hash differs from the approved final source")


def save_hybrid_artifacts(
    directory: Path,
    b_plan: MultiFramePlan,
    recurrence: RecurrenceMigrationPlan,
    recurrence_report: RecurrenceStudyReport,
    hybrid: HybridFinalReport,
) -> list[Path]:
    payloads = {
        "account-b-animation-plan.json": b_plan.model_dump_json(indent=2) + "\n",
        "account-b-recurrence-plan.json": recurrence.model_dump_json(indent=2) + "\n",
        "account-b-recurrence-report.json": recurrence_report.model_dump_json(indent=2) + "\n",
        "hybrid-final-plan.json": hybrid.model_dump_json(indent=2) + "\n",
        "hybrid-final-report.txt": build_hybrid_text(hybrid),
    }
    return [_write_atomic(directory / name, text) for name, text in payloads.items()]


def build_hybrid_text(report: HybridFinalReport) -> str:
    eta = {seconds: report.recurring_parents_b * seconds for seconds in (0.75, 1.0, 1.5, 2.0)}
    return "\n".join(
        [
            "FINAL HYBRID RECURRENCE PLAN",
            "============================",
            "",
            f"Run ID: {report.run_id}",
            "Frames A: indices 0-22 (human 1-23), existing singles, untouched",
            "Frames B: indices 23-107 (human 24-108), full frames via recurrence",
            "Frame 24 entirely B: YES",
            f"Rendered occurrences B: {report.logical_occurrences_b}",
            f"Independent-equivalent inserts B: {report.independent_equivalent_inserts_b}",
            f"Unique signatures B: {report.unique_recurrence_signatures_b}",
            f"Chunk: {report.recurrence_chunk_size}",
            f"Recurring parents B: {report.recurring_parents_b}",
            f"Reduction: {report.api_reduction_percent:.3f}%",
            f"Largest group: {report.largest_group}",
            f"Largest chunk: {report.largest_chunk}",
            f"Largest estimated payload: {report.largest_estimated_payload_bytes} bytes",
            "",
            "Capture: account-a 33%; account-b 90%; dedicated browser profiles",
            "Normalization: 504x288 logical-grid crop; nearest-neighbor only if required",
            "Transition validation: frame index 22 A vs frame index 23 B",
            "",
            f"ETA @0.75s/write: {eta[0.75]:.2f}s",
            f"ETA @1.0s/write: {eta[1.0]:.2f}s",
            f"ETA @1.5s/write: {eta[1.5]:.2f}s",
            f"ETA @2.0s/write: {eta[2.0]:.2f}s",
            "",
            "Bulk writes: NO",
            "Ordering gate: PASS",
            "",
        ]
    )


def _validate_source_invariants(plan: MultiFramePlan) -> None:
    expected = {
        "source_file": "input.mp4",
        "clip_start_seconds": 114.0,
        "clip_end_seconds": 150.0,
        "clip_duration_seconds": 36.0,
        "output_fps": 3.0,
        "frame_count": 108,
        "target_grid_width": 126,
        "target_grid_height": 72,
        "palette_preset": "cayde-final",
    }
    for field, value in expected.items():
        if getattr(plan, field) != value:
            raise CalendarAnimError(f"Final source invariant changed: {field}")
    if plan.event_compression.value != "synchronized-horizontal-bands":
        raise CalendarAnimError("Final source compression invariant changed")
    if plan.subcolumn_order_strategy.value != "zero-width":
        raise CalendarAnimError("Final source ordering invariant changed")


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
