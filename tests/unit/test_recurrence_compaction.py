import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from calendar_anim.calendar.models import CalendarEventDraft
from calendar_anim.calendar.multi_frame.models import (
    AnimationUploadState,
    FrameUploadState,
    FrameUploadStatus,
)
from calendar_anim.calendar.recurrence_compaction.models import RecurrenceSignature
from calendar_anim.calendar.recurrence_compaction.planner import (
    _build_parents,
    _missing_occurrences,
    _Occurrence,
    _recurrence_groups,
    recurrence_signature,
)

pytestmark = pytest.mark.unit


def _signature(
    *,
    day: int = 1,
    start: str = "08:00:00",
    duration: int = 1800,
    summary: str = "\u200b",
    color_id: str = "3",
) -> RecurrenceSignature:
    return RecurrenceSignature(
        timezone="America/Sao_Paulo",
        day_of_week=day,
        local_start_time=start,
        duration_seconds=duration,
        summary=summary,
        color_id=color_id,
    )


def _occurrence(
    key: str,
    week: int,
    *,
    signature: RecurrenceSignature | None = None,
    frame_index: int | None = None,
    original_event_id: str | None = None,
) -> _Occurrence:
    start = datetime(2027, 1, 5, 8, tzinfo=UTC) + timedelta(weeks=week)
    selected = signature or _signature()
    return _Occurrence(
        key=key,
        frame_index=week if frame_index is None else frame_index,
        start=start,
        end=start + timedelta(seconds=selected.duration_seconds),
        role="foreground",
        original_event_id=original_event_id or f"id-{key}",
        signature=selected,
    )


def test_same_visual_signature_across_weeks_uses_one_group() -> None:
    groups = _recurrence_groups([_occurrence("a", 0), _occurrence("b", 8)])

    assert [[item.key for item in group] for group in groups] == [["a", "b"]]


@pytest.mark.parametrize(
    "different",
    [
        _signature(color_id="4"),
        _signature(summary="\u200c"),
        _signature(start="08:30:00"),
        _signature(duration=3600),
        _signature(day=2),
    ],
)
def test_visual_signature_differences_require_different_parent(
    different: RecurrenceSignature,
) -> None:
    groups = _recurrence_groups([_occurrence("a", 0), _occurrence("b", 1, signature=different)])

    assert len(groups) == 2


def test_same_signature_and_absolute_start_preserves_multiplicity_in_separate_lanes() -> None:
    first = _occurrence("a", 0)
    duplicate = first.__class__(
        key="b",
        frame_index=first.frame_index,
        start=first.start,
        end=first.end,
        role=first.role,
        original_event_id="id-b",
        signature=first.signature,
    )

    groups = _recurrence_groups([first, duplicate])

    assert len(groups) == 2
    assert sorted(group[0].key for group in groups) == ["a", "b"]


def test_rdate_chunking_and_parent_ids_are_deterministic() -> None:
    occurrences = [_occurrence(f"k{index}", index) for index in range(5)]
    groups = _recurrence_groups(occurrences)

    first = _build_parents("run-1", "America/Sao_Paulo", groups, 2)
    second = _build_parents("run-1", "America/Sao_Paulo", groups, 2)

    assert first == second
    assert len(first) == 3
    assert [parent.occurrence_count for parent in first] == [2, 2, 1]
    assert first[0].recurrence == ["RDATE;TZID=America/Sao_Paulo:20270112T080000"]
    assert first[2].recurrence == []
    assert len({parent.parent_id for parent in first}) == 3


def test_completed_and_existing_single_occurrences_are_excluded() -> None:
    occurrences = [
        _occurrence("complete", 0, frame_index=0, original_event_id="complete-id"),
        _occurrence("existing", 1, frame_index=1, original_event_id="existing-id"),
        _occurrence("missing", 1, frame_index=1, original_event_id="missing-id"),
    ]
    state = AnimationUploadState(
        run_id="run-1",
        animation_id="animation",
        updated_at=datetime.now(UTC),
        frames=[
            FrameUploadState(
                frame_index=0,
                status=FrameUploadStatus.COMPLETED,
                planned_events=1,
                created_events=1,
                created_event_ids=["complete-id"],
            ),
            FrameUploadState(
                frame_index=1,
                status=FrameUploadStatus.PARTIAL,
                planned_events=2,
                created_events=1,
                created_event_ids=["existing-id"],
            ),
        ],
    )

    missing = _missing_occurrences(occurrences, state)

    assert [item.key for item in missing] == ["missing"]


def test_recurrence_expansion_reproduces_original_occurrence_set_once() -> None:
    occurrences = [_occurrence(f"k{index}", index) for index in range(12)]
    parents = _build_parents(
        "run-1",
        "America/Sao_Paulo",
        _recurrence_groups(occurrences),
        5,
    )
    expanded = [key for parent in parents for key in parent.occurrence_keys]

    assert len(expanded) == len(set(expanded)) == len(occurrences)
    assert set(expanded) == {item.key for item in occurrences}


def test_recurrence_signature_uses_visual_time_fields_not_absolute_week() -> None:
    start = datetime(2027, 1, 5, 8, tzinfo=UTC)
    base = CalendarEventDraft(
        frame_index=0,
        start=start,
        end=start + timedelta(minutes=30),
        color_id="3",
        summary="\u200b",
        private_metadata={"run_id": "frame-0"},
    )
    later = base.model_copy(
        update={
            "frame_index": 8,
            "start": start + timedelta(weeks=8),
            "end": start + timedelta(weeks=8, minutes=30),
            "private_metadata": {"run_id": "frame-8"},
        }
    )

    assert recurrence_signature("America/Sao_Paulo", base) == recurrence_signature(
        "America/Sao_Paulo", later
    )


def test_generated_final_run_artifact_accounts_for_all_277830_when_available() -> None:
    path = Path("output/recurrence-studies/cayde-final-126x72-3fps-36s-01/recurrence-report.json")
    if not path.exists():
        pytest.skip("local final-run recurrence study artifact is not present")
    report = json.loads(path.read_text(encoding="utf-8"))

    assert report["rendered_instances"] == 277830
    assert report["expanded_full_set_equals_original"] is True
    assert report["full_duplicate_occurrences"] == 0
    assert report["migration_expansion_equals_missing"] is True
