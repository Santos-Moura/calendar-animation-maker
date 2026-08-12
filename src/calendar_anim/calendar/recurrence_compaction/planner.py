import hashlib
import json
import math
import statistics
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import cast

from calendar_anim.calendar.event_identity import deterministic_event_id
from calendar_anim.calendar.frame_mapping.models import SingleFrameCalendarPlan
from calendar_anim.calendar.models import CalendarEventDraft
from calendar_anim.calendar.multi_frame.artifacts import AnimationRunStore
from calendar_anim.calendar.multi_frame.models import (
    AnimationUploadState,
    FrameUploadStatus,
    MultiFramePlan,
)
from calendar_anim.calendar.recurrence_compaction.models import (
    GroupDistribution,
    OccurrenceRole,
    PlannedOccurrence,
    RecurrenceMigrationPlan,
    RecurrenceSignature,
    RecurrenceStudyReport,
    RecurringParentPlan,
    ScopeCompaction,
)

DEFAULT_CHUNK_SIZES = (25, 50, 100, 250)

SIGNATURE_FIELDS_INCLUDED = [
    "timezone",
    "day_of_week",
    "local_start_time",
    "duration_seconds",
    "summary",
    "color_id",
    "transparency",
    "visibility",
    "event_type",
]

SIGNATURE_FIELDS_EXCLUDED = [
    "absolute_date_and_week",
    "color_hex_local_reference",
    "deterministic_single_event_id",
    "frame_index",
    "run_id_and_frame_private_metadata",
    "logical_coordinates_and_band_bookkeeping",
    "cell_role_non_visual_classification",
]


@dataclass(frozen=True, slots=True)
class _Occurrence:
    key: str
    frame_index: int
    start: datetime
    end: datetime
    role: OccurrenceRole
    original_event_id: str
    signature: RecurrenceSignature
    calendar_profile: str = "account-a"
    calendar_id: str | None = None


@dataclass(frozen=True, slots=True)
class RecurrenceStudyResult:
    report: RecurrenceStudyReport
    migration_plan: RecurrenceMigrationPlan


def build_recurrence_study(
    plan: MultiFramePlan,
    state: AnimationUploadState,
    store: AnimationRunStore,
    *,
    migration_chunk_size: int = 100,
    generated_at: datetime | None = None,
) -> RecurrenceStudyResult:
    if migration_chunk_size <= 0:
        raise ValueError("migration chunk size must be positive")
    generated_at = generated_at or datetime.now(UTC)
    all_occurrences = _load_occurrences(plan, store)
    if len(all_occurrences) != plan.total_events:
        raise ValueError("frame artifacts do not account for all planned events")

    full_groups = _recurrence_groups(all_occurrences)
    full_parent_counts = _parent_counts(full_groups, DEFAULT_CHUNK_SIZES)
    full_expanded = [occurrence.key for group in full_groups for occurrence in group]
    full_original = [occurrence.key for occurrence in all_occurrences]

    completed_frames = {
        frame.frame_index for frame in state.frames if frame.status is FrameUploadStatus.COMPLETED
    }
    existing_ids = {event_id for frame in state.frames for event_id in frame.created_event_ids}
    missing = _missing_occurrences(all_occurrences, state)
    migration_groups = _recurrence_groups(missing)
    migration_parents = _build_parents(
        plan.run_id,
        plan.timezone,
        migration_groups,
        migration_chunk_size,
    )
    migration_expanded = [key for parent in migration_parents for key in parent.occurrence_keys]
    missing_keys = [occurrence.key for occurrence in missing]
    migration_duplicates = len(migration_expanded) - len(set(migration_expanded))
    payload_sizes = [parent.estimated_insert_payload_bytes for parent in migration_parents]

    migration_plan = RecurrenceMigrationPlan(
        source_run_id=plan.run_id,
        generated_at=generated_at,
        timezone=plan.timezone,
        parent_chunk_size=migration_chunk_size,
        existing_single_event_ids=sorted(existing_ids),
        completed_frame_indices=sorted(completed_frames),
        partial_frame_indices=sorted(
            frame.frame_index for frame in state.frames if frame.status is FrameUploadStatus.PARTIAL
        ),
        remaining_occurrences=len(missing),
        parents=migration_parents,
        expanded_occurrence_count=len(migration_expanded),
        duplicate_occurrences=migration_duplicates,
        expansion_equals_missing=(
            migration_duplicates == 0 and set(migration_expanded) == set(missing_keys)
        ),
    )

    background = [item for item in all_occurrences if item.role == "background"]
    foreground = [item for item in all_occurrences if item.role == "foreground"]
    report = RecurrenceStudyReport(
        run_id=plan.run_id,
        generated_at=generated_at,
        timezone=plan.timezone,
        current_independent_inserts=plan.total_events,
        rendered_instances=len(all_occurrences),
        unique_exact_signatures=len({item.signature for item in all_occurrences}),
        distribution=_distribution(full_groups),
        full_scope=_scope(all_occurrences, full_groups, full_parent_counts),
        background=_scope_for_occurrences(background),
        foreground=_scope_for_occurrences(foreground),
        chunk_sizes=list(DEFAULT_CHUNK_SIZES),
        signature_fields_included=SIGNATURE_FIELDS_INCLUDED,
        signature_fields_excluded=SIGNATURE_FIELDS_EXCLUDED,
        expanded_full_set_equals_original=(
            len(full_expanded) == len(full_original)
            and len(full_expanded) == len(set(full_expanded))
            and set(full_expanded) == set(full_original)
        ),
        full_duplicate_occurrences=len(full_expanded) - len(set(full_expanded)),
        completed_frames_preserved=len(completed_frames),
        partial_single_events_preserved=sum(
            len(frame.created_event_ids)
            for frame in state.frames
            if frame.status is FrameUploadStatus.PARTIAL
        ),
        all_existing_single_events_preserved=len(existing_ids),
        remaining_occurrences=len(missing),
        migration_parent_chunk_size=migration_chunk_size,
        migration_parents_required=len(migration_parents),
        migration_insert_reduction=_reduction(len(missing), len(migration_parents)),
        migration_duplicate_occurrences=migration_duplicates,
        migration_expansion_equals_missing=migration_plan.expansion_equals_missing,
        largest_migration_payload_bytes=max(payload_sizes, default=0),
        mean_migration_payload_bytes=(statistics.fmean(payload_sizes) if payload_sizes else 0),
    )
    return RecurrenceStudyResult(report=report, migration_plan=migration_plan)


def expand_migration_plan(plan: RecurrenceMigrationPlan) -> set[str]:
    return {key for parent in plan.parents for key in parent.occurrence_keys}


def _load_occurrences(plan: MultiFramePlan, store: AnimationRunStore) -> list[_Occurrence]:
    occurrences: list[_Occurrence] = []
    for frame in plan.frames:
        frame_plan = store.load_frame_plan(plan, frame.frame_index)
        occurrences.extend(
            _frame_occurrences(
                plan.timezone,
                frame_plan,
                frame.calendar_profile or plan.calendar_profile,
                frame.calendar_id,
            )
        )
    return occurrences


def _missing_occurrences(
    occurrences: Sequence[_Occurrence],
    state: AnimationUploadState,
) -> list[_Occurrence]:
    completed_frames = {
        frame.frame_index for frame in state.frames if frame.status is FrameUploadStatus.COMPLETED
    }
    existing_ids = {event_id for frame in state.frames for event_id in frame.created_event_ids}
    return [
        occurrence
        for occurrence in occurrences
        if occurrence.frame_index not in completed_frames
        and occurrence.original_event_id not in existing_ids
    ]


def _frame_occurrences(
    timezone: str,
    frame_plan: SingleFrameCalendarPlan,
    calendar_profile: str = "account-a",
    calendar_id: str | None = None,
) -> Iterable[_Occurrence]:
    for event in frame_plan.events:
        frame_index = event.frame_index if event.frame_index is not None else frame_plan.frame_index
        role_value = event.private_metadata.get("cell_role", "unknown")
        role = cast(
            OccurrenceRole,
            role_value if role_value in {"background", "foreground"} else "unknown",
        )
        signature = recurrence_signature(timezone, event)
        original_id = deterministic_event_id(event)
        yield _Occurrence(
            key=_occurrence_key(frame_index, original_id),
            frame_index=frame_index,
            start=event.start,
            end=event.end,
            role=role,
            original_event_id=original_id,
            signature=signature,
            calendar_profile=calendar_profile,
            calendar_id=calendar_id,
        )


def recurrence_signature(
    timezone: str,
    event: CalendarEventDraft,
) -> RecurrenceSignature:
    duration = int((event.end - event.start).total_seconds())
    return RecurrenceSignature(
        timezone=timezone,
        day_of_week=event.start.weekday(),
        local_start_time=event.start.strftime("%H:%M:%S"),
        duration_seconds=duration,
        summary=event.summary,
        color_id=event.color_id,
    )


def _recurrence_groups(occurrences: Sequence[_Occurrence]) -> list[list[_Occurrence]]:
    by_signature: dict[tuple[str, str | None, RecurrenceSignature], list[_Occurrence]] = (
        defaultdict(list)
    )
    for occurrence in occurrences:
        key = (occurrence.calendar_profile, occurrence.calendar_id, occurrence.signature)
        by_signature[key].append(occurrence)

    groups: list[list[_Occurrence]] = []
    for key in sorted(
        by_signature,
        key=lambda item: (item[0], item[1] or "", _signature_canonical(item[2])),
    ):
        by_start: dict[datetime, list[_Occurrence]] = defaultdict(list)
        for occurrence in by_signature[key]:
            by_start[occurrence.start].append(occurrence)
        lanes: list[list[_Occurrence]] = []
        for start in sorted(by_start):
            same_start = sorted(by_start[start], key=lambda item: item.key)
            while len(lanes) < len(same_start):
                lanes.append([])
            for lane_index, occurrence in enumerate(same_start):
                lanes[lane_index].append(occurrence)
        groups.extend(lanes)
    return groups


def _build_parents(
    run_id: str,
    timezone: str,
    groups: Sequence[Sequence[_Occurrence]],
    chunk_size: int,
) -> list[RecurringParentPlan]:
    parents: list[RecurringParentPlan] = []
    for group in groups:
        ordered = sorted(group, key=lambda item: (item.start, item.key))
        signature = ordered[0].signature
        signature_hash = _short_hash(_signature_canonical(signature))
        recurrence_group_id = f"rg{signature_hash}{_short_hash('|'.join(x.key for x in ordered))}"
        for chunk_index, start_index in enumerate(range(0, len(ordered), chunk_size)):
            chunk = ordered[start_index : start_index + chunk_size]
            occurrence_keys = [item.key for item in chunk]
            parent_id = _parent_id(
                run_id,
                signature_hash,
                recurrence_group_id,
                chunk_index,
                occurrence_keys,
            )
            recurrence = _rdate_lines(timezone, [item.start for item in chunk[1:]])
            private_metadata = {
                "generated_by": "calendar-anim",
                "run_id": run_id,
                "recurrence_group_id": recurrence_group_id,
                "signature_hash": signature_hash,
                "chunk_index": str(chunk_index),
                "calendar_profile": chunk[0].calendar_profile,
            }
            payload = {
                "id": parent_id,
                "summary": signature.summary,
                "start": {
                    "dateTime": chunk[0].start.isoformat(),
                    "timeZone": timezone,
                },
                "end": {
                    "dateTime": chunk[0].end.isoformat(),
                    "timeZone": timezone,
                },
                "colorId": signature.color_id,
                "transparency": signature.transparency,
                "visibility": signature.visibility,
                "eventType": signature.event_type,
                "recurrence": recurrence,
                "extendedProperties": {"private": private_metadata},
            }
            parents.append(
                RecurringParentPlan(
                    parent_id=parent_id,
                    recurrence_group_id=recurrence_group_id,
                    signature_hash=signature_hash,
                    chunk_index=chunk_index,
                    signature=signature,
                    start=chunk[0].start,
                    end=chunk[0].end,
                    recurrence=recurrence,
                    occurrence_keys=occurrence_keys,
                    covered_frame_indices=sorted({item.frame_index for item in chunk}),
                    private_metadata=private_metadata,
                    estimated_insert_payload_bytes=len(
                        json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode(
                            "utf-8"
                        )
                    ),
                    calendar_profile=chunk[0].calendar_profile,
                    calendar_id=chunk[0].calendar_id,
                )
            )
    return parents


def _rdate_lines(timezone: str, starts: Sequence[datetime]) -> list[str]:
    if not starts:
        return []
    values = ",".join(start.strftime("%Y%m%dT%H%M%S") for start in starts)
    return [f"RDATE;TZID={timezone}:{values}"]


def _parent_counts(
    groups: Sequence[Sequence[_Occurrence]],
    chunk_sizes: Sequence[int],
) -> dict[int, int]:
    return {
        chunk_size: sum(math.ceil(len(group) / chunk_size) for group in groups)
        for chunk_size in chunk_sizes
    }


def _scope_for_occurrences(occurrences: Sequence[_Occurrence]) -> ScopeCompaction:
    groups = _recurrence_groups(occurrences)
    return _scope(occurrences, groups, _parent_counts(groups, DEFAULT_CHUNK_SIZES))


def _scope(
    occurrences: Sequence[_Occurrence],
    groups: Sequence[Sequence[_Occurrence]],
    parent_counts: dict[int, int],
) -> ScopeCompaction:
    total = len(occurrences)
    unlimited = len(groups)
    reductions = {"unlimited": _reduction(total, unlimited)}
    reductions.update(
        {str(chunk): _reduction(total, count) for chunk, count in parent_counts.items()}
    )
    return ScopeCompaction(
        occurrences=total,
        unique_signatures=len({item.signature for item in occurrences}),
        parents_unlimited=unlimited,
        parents_by_chunk=parent_counts,
        reduction_by_chunk=reductions,
    )


def _distribution(groups: Sequence[Sequence[_Occurrence]]) -> GroupDistribution:
    sizes = sorted(len(group) for group in groups)
    if not sizes:
        return GroupDistribution(
            singleton=0,
            two_to_five=0,
            six_to_ten=0,
            eleven_to_twenty_five=0,
            twenty_six_to_fifty=0,
            fifty_one_to_one_hundred=0,
            over_one_hundred=0,
            mean=0,
            median=0,
            p95=0,
            largest=0,
        )
    p95_index = max(0, math.ceil(0.95 * len(sizes)) - 1)
    return GroupDistribution(
        singleton=sum(size == 1 for size in sizes),
        two_to_five=sum(2 <= size <= 5 for size in sizes),
        six_to_ten=sum(6 <= size <= 10 for size in sizes),
        eleven_to_twenty_five=sum(11 <= size <= 25 for size in sizes),
        twenty_six_to_fifty=sum(26 <= size <= 50 for size in sizes),
        fifty_one_to_one_hundred=sum(51 <= size <= 100 for size in sizes),
        over_one_hundred=sum(size > 100 for size in sizes),
        mean=statistics.fmean(sizes),
        median=statistics.median(sizes),
        p95=float(sizes[p95_index]),
        largest=sizes[-1],
    )


def _signature_canonical(signature: RecurrenceSignature) -> str:
    return signature.model_dump_json()


def _occurrence_key(frame_index: int, original_event_id: str) -> str:
    return f"f{frame_index:04d}:{original_event_id}"


def _short_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def _parent_id(
    run_id: str,
    signature_hash: str,
    recurrence_group_id: str,
    chunk_index: int,
    occurrence_keys: Sequence[str],
) -> str:
    canonical = json.dumps(
        {
            "run_id": run_id,
            "signature_hash": signature_hash,
            "recurrence_group_id": recurrence_group_id,
            "chunk_index": chunk_index,
            "occurrence_keys": list(occurrence_keys),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return f"cr{hashlib.sha256(canonical.encode('utf-8')).hexdigest()}"


def _reduction(occurrences: int, parents: int) -> float:
    if occurrences == 0:
        return 0.0
    return 100.0 * (1.0 - parents / occurrences)


def occurrence_model(occurrence: _Occurrence) -> PlannedOccurrence:
    return PlannedOccurrence(
        occurrence_key=occurrence.key,
        frame_index=occurrence.frame_index,
        start=occurrence.start,
        end=occurrence.end,
        role=occurrence.role,
        original_event_id=occurrence.original_event_id,
        calendar_profile=occurrence.calendar_profile,
        calendar_id=occurrence.calendar_id,
    )
