from datetime import UTC, date, datetime, timedelta

from calendar_anim.calendar.recurrence_compaction.models import (
    RecurrenceMigrationPlan,
    RecurrenceSignature,
    RecurringParentPlan,
)
from calendar_anim.calendar.remote_recurrence_audit.gateway import (
    GoogleRemoteRecurrenceAuditGateway,
)
from calendar_anim.calendar.remote_recurrence_audit.models import (
    ExpectedOccurrence,
    RemoteOccurrence,
)
from calendar_anim.calendar.remote_recurrence_audit.service import (
    _compare_frame,
    _parent_dates,
    _plan_invariants,
)


def _parent(*, base_in_rdate: bool = False) -> RecurringParentPlan:
    start = datetime(2028, 3, 20, 6, tzinfo=UTC)
    rdates = [start] if base_in_rdate else []
    rdates.append(start + timedelta(days=7))
    recurrence = ["RDATE;TZID=UTC:" + ",".join(value.strftime("%Y%m%dT%H%M%S") for value in rdates)]
    count = len(rdates) + 1
    return RecurringParentPlan(
        parent_id="cr" + "1" * 64,
        recurrence_group_id="group",
        signature_hash="hash",
        chunk_index=0,
        signature=RecurrenceSignature(
            timezone="UTC",
            day_of_week=0,
            local_start_time="06:00:00",
            duration_seconds=900,
            summary="\u200b\u2060",
            color_id="1",
        ),
        start=start,
        end=start + timedelta(minutes=15),
        recurrence=recurrence,
        occurrence_keys=[f"key-{index}" for index in range(count)],
        covered_frame_indices=list(range(count)),
        private_metadata={"run_id": "run", "calendar_profile": "account-b"},
        estimated_insert_payload_bytes=500,
        calendar_profile="account-b",
    )


def _plan(parent: RecurringParentPlan) -> RecurrenceMigrationPlan:
    return RecurrenceMigrationPlan(
        source_run_id="run",
        generated_at=datetime.now(UTC),
        timezone="UTC",
        parent_chunk_size=100,
        existing_single_event_ids=[],
        completed_frame_indices=[],
        partial_frame_indices=[],
        remaining_occurrences=parent.occurrence_count,
        parents=[parent],
        expanded_occurrence_count=parent.occurrence_count,
        duplicate_occurrences=0,
        expansion_equals_missing=True,
    )


def _expected() -> ExpectedOccurrence:
    start = datetime(2028, 3, 20, 6, tzinfo=UTC)
    return ExpectedOccurrence(
        occurrence_key="key",
        parent_id="parent",
        chunk_index=0,
        frame_index=23,
        start=start,
        end=start + timedelta(minutes=15),
        timezone="UTC",
        summary="\u200b\u2060",
        summary_codepoints=["U+200B", "U+2060"],
        color_id="1",
        role="background",
    )


def _remote(**changes: object) -> RemoteOccurrence:
    expected = _expected()
    values: dict[str, object] = {
        "event_id": "instance",
        "parent_id": "parent",
        "recurring_event_id": "parent",
        "original_start_time": expected.start,
        "start": expected.start,
        "end": expected.end,
        "start_timezone": "UTC",
        "end_timezone": "UTC",
        "summary": expected.summary,
        "summary_codepoints": expected.summary_codepoints,
        "color_id": expected.color_id,
        "private_metadata": {"run_id": "run"},
    }
    values.update(changes)
    return RemoteOccurrence.model_validate(values)


def test_exact_remote_expansion_matches_expected_visual_occurrence() -> None:
    result = _compare_frame(24, 23, date(2028, 3, 19), [_expected()], [_remote()])

    assert result.exact_matches == 1
    assert result.expected_set == [_expected()]
    assert result.remote_expanded_set == [_remote()]
    assert result.missing == result.extra == result.duplicates == 0
    assert result.wrong_time == result.wrong_summary == result.wrong_color == 0


def test_remote_diff_reports_time_summary_and_color_concretely() -> None:
    expected = _expected()
    remote = _remote(
        start=expected.start + timedelta(minutes=15),
        end=expected.end + timedelta(minutes=15),
        summary="changed",
        summary_codepoints=["U+0063"],
        color_id="3",
    )

    result = _compare_frame(24, 23, date(2028, 3, 19), [expected], [remote])

    assert result.exact_matches == 0
    assert result.missing == result.extra == 1
    assert result.wrong_time == result.wrong_summary == result.wrong_color == 1
    assert result.first_divergences[0].differing_fields == [
        "start/end",
        "summary",
        "colorId",
    ]


def test_singleton_parents_without_recurring_event_id_are_not_duplicates() -> None:
    first = _expected()
    second = first.model_copy(update={"occurrence_key": "key-2", "parent_id": "parent-2"})
    first_remote = _remote(recurring_event_id=None)
    second_remote = _remote(
        event_id="parent-2",
        parent_id="parent-2",
        recurring_event_id=None,
    )

    result = _compare_frame(
        24,
        23,
        date(2028, 3, 19),
        [first, second],
        [first_remote, second_remote],
    )

    assert result.exact_matches == 2
    assert result.duplicates == 0
    assert result.wrong_parent_mapping == 0


def test_rdate_base_is_implicit_and_not_duplicated_in_valid_chunk() -> None:
    parent = _parent()

    dates = _parent_dates(parent)
    invariants = _plan_invariants(_plan(parent))

    assert dates == [parent.start, parent.start + timedelta(days=7)]
    assert invariants.base_in_rdate_count == 0
    assert invariants.recurrence_cardinality_mismatches == 0


def test_rdate_base_duplication_is_detected() -> None:
    invariants = _plan_invariants(_plan(_parent(base_in_rdate=True)))

    assert invariants.base_in_rdate_count == 1


class Response:
    def __init__(self, value: dict[str, object]) -> None:
        self.value = value

    def execute(self) -> dict[str, object]:
        return self.value


class ReadOnlyEvents:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []

    def list(self, **kwargs: object) -> Response:
        self.calls.append(("list", kwargs))
        return Response({"items": [{"id": "instance"}]})

    def get(self, **kwargs: object) -> Response:
        self.calls.append(("get", kwargs))
        return Response({"id": kwargs["eventId"]})

    def insert(self, **_kwargs: object) -> Response:
        raise AssertionError("read-only audit must never call events.insert")

    def update(self, **_kwargs: object) -> Response:
        raise AssertionError("read-only audit must never call events.update")

    def delete(self, **_kwargs: object) -> Response:
        raise AssertionError("read-only audit must never call events.delete")


class ReadOnlyGoogleService:
    def __init__(self) -> None:
        self.resource = ReadOnlyEvents()

    def events(self) -> ReadOnlyEvents:
        return self.resource


def test_google_audit_gateway_uses_only_list_and_get() -> None:
    google = ReadOnlyGoogleService()
    gateway = GoogleRemoteRecurrenceAuditGateway(google)
    start = datetime(2028, 3, 19, tzinfo=UTC)

    gateway.list_expanded_window("calendar", start, start + timedelta(days=7))
    gateway.get_parent_resource("calendar", "parent")

    assert [name for name, _kwargs in google.resource.calls] == ["list", "get"]
    listed = google.resource.calls[0][1]
    assert listed["singleEvents"] is True
    assert listed["showDeleted"] is False
