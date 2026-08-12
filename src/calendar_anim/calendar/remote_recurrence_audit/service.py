from collections import Counter, defaultdict
from collections.abc import Sequence
from datetime import UTC, date, datetime, time, timedelta
from typing import Any, Protocol
from zoneinfo import ZoneInfo

from calendar_anim.calendar.event_identity import deterministic_event_id
from calendar_anim.calendar.multi_frame.artifacts import AnimationRunStore
from calendar_anim.calendar.multi_frame.models import MultiFramePlan
from calendar_anim.calendar.recurrence_compaction.models import (
    RecurrenceMigrationPlan,
    RecurringParentPlan,
)
from calendar_anim.calendar.remote_recurrence_audit.models import (
    Divergence,
    ExpectedOccurrence,
    FrameRemoteAudit,
    ParentAudit,
    PlanInvariantAudit,
    RemoteOccurrence,
    RemoteRecurrenceAuditReport,
)
from calendar_anim.exceptions import CalendarAnimError


class RemoteAuditGateway(Protocol):
    def list_expanded_window(
        self, calendar_id: str, start: datetime, end: datetime
    ) -> list[dict[str, Any]]: ...

    def get_parent_resource(self, calendar_id: str, parent_id: str) -> dict[str, Any] | None: ...


class RemoteRecurrenceAuditService:
    def __init__(
        self,
        gateway: RemoteAuditGateway,
        animation_store: AnimationRunStore | None = None,
    ) -> None:
        self.gateway = gateway
        self.animation_store = animation_store or AnimationRunStore()

    def audit(
        self,
        *,
        run_id: str,
        profile: str,
        calendar_name: str,
        calendar_id: str,
        recurrence_plan: RecurrenceMigrationPlan,
        animation_artifact: dict[str, object],
        source_plan: MultiFramePlan,
        human_frames: Sequence[int],
    ) -> RemoteRecurrenceAuditReport:
        if profile != "account-b":
            raise CalendarAnimError("Remote recurrence audit is restricted to account-b")
        if recurrence_plan.source_run_id != run_id:
            raise CalendarAnimError("Recurrence plan belongs to another hybrid run")
        frame_metadata = _account_b_frame_metadata(animation_artifact)
        selected_indices = [human_frame - 1 for human_frame in human_frames]
        expected_by_frame, parents_by_key = self._expected_occurrences(
            recurrence_plan, source_plan, selected_indices
        )
        all_parent_ids = {parent.parent_id for parent in recurrence_plan.parents}
        frame_results: list[FrameRemoteAudit] = []
        remote_by_parent: dict[str, list[RemoteOccurrence]] = defaultdict(list)
        for human_frame, frame_index in zip(human_frames, selected_indices, strict=True):
            metadata = frame_metadata.get(frame_index)
            if metadata is None:
                raise CalendarAnimError(f"Account-B animation artifact has no frame {frame_index}")
            week_start = date.fromisoformat(str(metadata["week_start"]))
            expected = expected_by_frame[frame_index]
            if len(expected) != _required_int(metadata, "planned_events"):
                raise CalendarAnimError(
                    f"Frame {human_frame} source plan count differs from animation artifact"
                )
            remote = self._remote_week(
                calendar_id,
                recurrence_plan.timezone,
                week_start,
                run_id,
                all_parent_ids,
            )
            for occurrence in remote:
                remote_by_parent[occurrence.parent_id].append(occurrence)
            frame_results.append(
                _compare_frame(human_frame, frame_index, week_start, expected, remote)
            )
        broken_parent_ids = _broken_parent_ids(frame_results)
        if not broken_parent_ids:
            broken_parent_ids = _representative_parent_ids(expected_by_frame, selected_indices)
        parent_by_id = {parent.parent_id: parent for parent in recurrence_plan.parents}
        parent_audits = self._audit_parents(
            calendar_id,
            [parent_by_id[parent_id] for parent_id in broken_parent_ids[:12]],
            remote_by_parent,
        )
        audits_by_id = {item.parent_id: item for item in parent_audits}
        for frame in frame_results:
            ids = {
                item.parent_id for item in frame.first_divergences if item.parent_id in audits_by_id
            }
            if not ids:
                ids = {
                    item.parent_id
                    for item in expected_by_frame[frame.frame_index]
                    if item.parent_id in audits_by_id
                }
            frame.parent_audits = [audits_by_id[parent_id] for parent_id in sorted(ids)]
        invariants = _plan_invariants(recurrence_plan)
        category, reason, recurrence_broken, grouping_wrong, salvageable = _classify(
            frame_results, parent_audits, invariants
        )
        return RemoteRecurrenceAuditReport(
            run_id=run_id,
            profile=profile,
            calendar_name=calendar_name,
            calendar_id=calendar_id,
            timezone=recurrence_plan.timezone,
            generated_at=datetime.now(UTC),
            frames_audited=list(human_frames),
            frames=frame_results,
            total_expected_occurrences=sum(item.expected_occurrences for item in frame_results),
            total_google_expanded_occurrences=sum(
                item.google_expanded_occurrences for item in frame_results
            ),
            total_exact_matches=sum(item.exact_matches for item in frame_results),
            total_missing=sum(item.missing for item in frame_results),
            total_extra=sum(item.extra for item in frame_results),
            total_duplicates=sum(item.duplicates for item in frame_results),
            plan_invariants=invariants,
            root_cause_category=category,
            root_cause=reason,
            recurrence_mechanism_broken=recurrence_broken,
            planner_grouping_wrong=grouping_wrong,
            existing_bulk_salvageable_without_recreation=salvageable,
        )

    def _expected_occurrences(
        self,
        recurrence_plan: RecurrenceMigrationPlan,
        source_plan: MultiFramePlan,
        selected_indices: Sequence[int],
    ) -> tuple[dict[int, list[ExpectedOccurrence]], dict[str, RecurringParentPlan]]:
        draft_by_key: dict[str, tuple[int, Any]] = {}
        for frame_index in selected_indices:
            frame = self.animation_store.load_frame_plan(source_plan, frame_index)
            for event in frame.events:
                actual_index = event.frame_index if event.frame_index is not None else frame_index
                key = f"f{actual_index:04d}:{deterministic_event_id(event)}"
                if key in draft_by_key:
                    raise CalendarAnimError(f"Duplicate selected occurrence key: {key}")
                draft_by_key[key] = (frame_index, event)
        parent_by_key: dict[str, RecurringParentPlan] = {}
        selected_keys = set(draft_by_key)
        for parent in recurrence_plan.parents:
            for key in parent.occurrence_keys:
                if key in selected_keys:
                    parent_by_key[key] = parent
        missing_keys = selected_keys - set(parent_by_key)
        if missing_keys:
            raise CalendarAnimError(
                f"Recurrence plan does not cover {len(missing_keys)} selected occurrences"
            )
        result: dict[int, list[ExpectedOccurrence]] = defaultdict(list)
        for key, (frame_index, event) in draft_by_key.items():
            parent = parent_by_key[key]
            result[frame_index].append(
                ExpectedOccurrence(
                    occurrence_key=key,
                    parent_id=parent.parent_id,
                    chunk_index=parent.chunk_index,
                    frame_index=frame_index,
                    start=event.start,
                    end=event.end,
                    timezone=recurrence_plan.timezone,
                    summary=event.summary,
                    summary_codepoints=_codepoints(event.summary),
                    color_id=event.color_id,
                    role=event.private_metadata.get("cell_role", "unknown"),
                )
            )
        for occurrences in result.values():
            occurrences.sort(key=lambda item: (_visual_key(item), item.parent_id))
        return dict(result), parent_by_key

    def _remote_week(
        self,
        calendar_id: str,
        timezone: str,
        week_start: date,
        run_id: str,
        parent_ids: set[str],
    ) -> list[RemoteOccurrence]:
        zone = ZoneInfo(timezone)
        start = datetime.combine(week_start, time.min, zone)
        end = start + timedelta(days=7)
        raw = self.gateway.list_expanded_window(calendar_id, start, end)
        result: list[RemoteOccurrence] = []
        for item in raw:
            metadata = _private_metadata(item)
            parent_id = _optional_string(item.get("recurringEventId"))
            if parent_id not in parent_ids and metadata.get("run_id") != run_id:
                continue
            result.append(_remote_occurrence(item, zone, parent_ids))
        return sorted(result, key=lambda item: (_visual_key(item), item.event_id))

    def _audit_parents(
        self,
        calendar_id: str,
        parents: Sequence[RecurringParentPlan],
        remote_by_parent: dict[str, list[RemoteOccurrence]],
    ) -> list[ParentAudit]:
        audits: list[ParentAudit] = []
        for parent in parents:
            remote = self.gateway.get_parent_resource(calendar_id, parent.parent_id)
            expected_dates = _parent_dates(parent)
            remote_dates = sorted(
                {
                    (item.original_start_time or item.start).isoformat()
                    for item in remote_by_parent.get(parent.parent_id, [])
                }
            )
            audits.append(_parent_audit(parent, remote, expected_dates, remote_dates))
        return audits


def _compare_frame(
    human_frame: int,
    frame_index: int,
    week_start: date,
    expected: list[ExpectedOccurrence],
    remote: list[RemoteOccurrence],
) -> FrameRemoteAudit:
    expected_counter = Counter(_visual_key(item) for item in expected)
    remote_counter = Counter(_visual_key(item) for item in remote)
    exact_matches = sum((expected_counter & remote_counter).values())
    missing_counter = expected_counter - remote_counter
    extra_counter = remote_counter - expected_counter
    missing_items = _counter_items(expected, missing_counter)
    extra_items = _counter_items(remote, extra_counter)
    divergences: list[Divergence] = []
    expected_anchor: dict[tuple[str, str], ExpectedOccurrence] = {}
    for expected_item in expected:
        expected_anchor[(expected_item.parent_id, expected_item.start.isoformat())] = expected_item
    wrong_date = wrong_time = wrong_summary = wrong_color = wrong_parent = 0
    for remote_item in remote:
        parent_id = remote_item.parent_id
        anchor_time = remote_item.original_start_time or remote_item.start
        if not parent_id:
            wrong_parent += 1
            continue
        paired = expected_anchor.get((parent_id, anchor_time.isoformat()))
        if paired is None:
            wrong_date += 1
            continue
        fields: list[str] = []
        if paired.start != remote_item.start or paired.end != remote_item.end:
            wrong_time += 1
            fields.append("start/end")
        if paired.summary != remote_item.summary:
            wrong_summary += 1
            fields.append("summary")
        if paired.color_id != remote_item.color_id:
            wrong_color += 1
            fields.append("colorId")
        if fields and len(divergences) < 20:
            divergences.append(
                Divergence(
                    category="field_mismatch",
                    parent_id=parent_id,
                    expected=paired,
                    remote=remote_item,
                    differing_fields=fields,
                )
            )
    for item in missing_items:
        if len(divergences) >= 20:
            break
        divergences.append(Divergence(category="missing", parent_id=item.parent_id, expected=item))
    for item in extra_items:
        if len(divergences) >= 20:
            break
        divergences.append(Divergence(category="extra", parent_id=item.parent_id, remote=item))
    identities = Counter(
        (
            item.parent_id,
            (item.original_start_time or item.start).isoformat(),
        )
        for item in remote
    )
    duplicates = sum(count - 1 for count in identities.values() if count > 1)
    return FrameRemoteAudit(
        human_frame=human_frame,
        frame_index=frame_index,
        week_start=week_start,
        expected_occurrences=len(expected),
        google_expanded_occurrences=len(remote),
        exact_matches=exact_matches,
        missing=sum(missing_counter.values()),
        extra=sum(extra_counter.values()),
        duplicates=duplicates,
        wrong_date=wrong_date,
        wrong_time=wrong_time,
        wrong_summary=wrong_summary,
        wrong_color=wrong_color,
        wrong_parent_mapping=wrong_parent,
        expected_set=expected,
        remote_expanded_set=remote,
        first_divergences=divergences,
        parent_audits=[],
    )


def _remote_occurrence(
    item: dict[str, Any], zone: ZoneInfo, parent_ids: set[str]
) -> RemoteOccurrence:
    summary = str(item.get("summary", ""))
    event_id = str(item.get("id", ""))
    recurring_event_id = _optional_string(item.get("recurringEventId"))
    parent_id = recurring_event_id or (event_id if event_id in parent_ids else "")
    return RemoteOccurrence(
        event_id=event_id,
        parent_id=parent_id,
        recurring_event_id=recurring_event_id,
        original_start_time=_api_datetime(item.get("originalStartTime"), zone, required=False),
        start=_api_datetime(item.get("start"), zone, required=True),
        end=_api_datetime(item.get("end"), zone, required=True),
        start_timezone=_api_timezone(item.get("start")),
        end_timezone=_api_timezone(item.get("end")),
        summary=summary,
        summary_codepoints=_codepoints(summary),
        color_id=_optional_string(item.get("colorId")),
        private_metadata=_private_metadata(item),
    )


def _api_datetime(value: object, zone: ZoneInfo, *, required: bool) -> datetime | None:
    if not isinstance(value, dict):
        if required:
            raise CalendarAnimError("Remote Calendar occurrence has no timed start/end")
        return None
    raw = value.get("dateTime")
    if not isinstance(raw, str):
        if required:
            raise CalendarAnimError("Remote Calendar occurrence is unexpectedly all-day")
        return None
    parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=zone)
    return parsed.astimezone(zone)


def _api_timezone(value: object) -> str | None:
    return _optional_string(value.get("timeZone")) if isinstance(value, dict) else None


def _private_metadata(item: dict[str, Any]) -> dict[str, str]:
    extended = item.get("extendedProperties", {})
    private = extended.get("private", {}) if isinstance(extended, dict) else {}
    if not isinstance(private, dict):
        return {}
    return {str(key): str(value) for key, value in private.items()}


def _visual_key(item: ExpectedOccurrence | RemoteOccurrence) -> tuple[str, str, str, str]:
    return (
        item.start.isoformat(),
        item.end.isoformat(),
        item.summary,
        item.color_id or "",
    )


def _counter_items(items: Sequence[Any], counter: Counter[Any]) -> list[Any]:
    remaining = counter.copy()
    result: list[Any] = []
    for item in items:
        key = _visual_key(item)
        if remaining[key] > 0:
            result.append(item)
            remaining[key] -= 1
    return result


def _parent_dates(parent: RecurringParentPlan) -> list[datetime]:
    dates = [parent.start]
    zone = ZoneInfo(parent.signature.timezone)
    for line in parent.recurrence:
        try:
            prefix, values = line.split(":", 1)
            timezone = prefix.split("TZID=", 1)[1]
        except (IndexError, ValueError) as error:
            raise CalendarAnimError(f"Invalid RDATE on parent {parent.parent_id}") from error
        if timezone != parent.signature.timezone:
            raise CalendarAnimError(f"RDATE timezone mismatch on parent {parent.parent_id}")
        dates.extend(
            datetime.strptime(value, "%Y%m%dT%H%M%S").replace(tzinfo=zone)
            for value in values.split(",")
        )
    return dates


def _parent_audit(
    parent: RecurringParentPlan,
    remote: dict[str, Any] | None,
    expected_dates: list[datetime],
    remote_dates: list[str],
) -> ParentAudit:
    remote_start = remote.get("start") if remote else None
    remote_end = remote.get("end") if remote else None
    remote_recurrence = list(remote.get("recurrence", [])) if remote else []
    remote_summary = str(remote.get("summary", "")) if remote else None
    remote_color = _optional_string(remote.get("colorId")) if remote else None
    remote_transparency = str(remote.get("transparency", "opaque")) if remote else None
    remote_visibility = str(remote.get("visibility", "default")) if remote else None
    remote_event_type = str(remote.get("eventType", "default")) if remote else None
    remote_metadata = _private_metadata(remote) if remote else {}
    payload_matches = bool(
        remote
        and remote_start
        == {"dateTime": parent.start.isoformat(), "timeZone": parent.signature.timezone}
        and remote_end
        == {"dateTime": parent.end.isoformat(), "timeZone": parent.signature.timezone}
        and remote_recurrence == parent.recurrence
        and remote_summary == parent.signature.summary
        and remote_color == parent.signature.color_id
        and remote_transparency == parent.signature.transparency
        and remote_visibility == parent.signature.visibility
        and remote_event_type == parent.signature.event_type
        and remote_metadata == parent.private_metadata
    )
    return ParentAudit(
        parent_id=parent.parent_id,
        chunk_index=parent.chunk_index,
        local_dtstart=parent.start,
        local_dtend=parent.end,
        local_recurrence=parent.recurrence,
        local_occurrence_count=parent.occurrence_count,
        local_expected_dates=[value.isoformat() for value in expected_dates],
        base_in_rdate=parent.start in expected_dates[1:],
        remote_found=remote is not None,
        remote_dtstart=remote_start if isinstance(remote_start, dict) else None,
        remote_dtend=remote_end if isinstance(remote_end, dict) else None,
        remote_recurrence=remote_recurrence,
        remote_summary=remote_summary,
        remote_summary_codepoints=_codepoints(remote_summary or ""),
        remote_color_id=remote_color,
        remote_transparency=remote_transparency,
        remote_visibility=remote_visibility,
        remote_event_type=remote_event_type,
        remote_private_metadata=remote_metadata,
        payload_matches=payload_matches,
        remote_occurrence_dates=remote_dates,
    )


def _plan_invariants(plan: RecurrenceMigrationPlan) -> PlanInvariantAudit:
    chunk_violations = base_in_rdate = cardinality = unsorted = 0
    for parent in plan.parents:
        dates = _parent_dates(parent)
        chunk_violations += parent.occurrence_count > plan.parent_chunk_size
        base_in_rdate += parent.start in dates[1:]
        cardinality += len(dates) != parent.occurrence_count
        unsorted += dates != sorted(dates)
    return PlanInvariantAudit(
        parents_checked=len(plan.parents),
        chunk_size=plan.parent_chunk_size,
        chunk_size_violations=chunk_violations,
        base_in_rdate_count=base_in_rdate,
        recurrence_cardinality_mismatches=cardinality,
        unsorted_rdate_parents=unsorted,
        signature_fields_included=[
            "day_of_week",
            "local_start_time",
            "duration_seconds",
            "summary",
            "color_id",
            "timezone",
            "transparency",
            "visibility",
            "event_type",
        ],
        signature_fields_excluded=[
            "logical_day",
            "horizontal_lane",
            "band_identity",
            "frame_index",
            "week_start",
        ],
    )


def _classify(
    frames: Sequence[FrameRemoteAudit],
    parents: Sequence[ParentAudit],
    invariants: PlanInvariantAudit,
) -> tuple[str, str, str, str, str]:
    if (
        invariants.chunk_size_violations
        or invariants.base_in_rdate_count
        or invariants.recurrence_cardinality_mismatches
    ):
        return (
            "D",
            "Local chunk/RDATE invariants are violated.",
            "PARTIAL",
            "YES",
            "UNKNOWN",
        )
    if any(not parent.remote_found or not parent.payload_matches for parent in parents):
        return (
            "B",
            "At least one stored Google parent differs from the locked local payload.",
            "PARTIAL",
            "NO",
            "UNKNOWN",
        )
    if all(
        frame.missing == frame.extra == frame.duplicates == 0
        and frame.exact_matches == frame.expected_occurrences
        for frame in frames
    ):
        return (
            "F",
            "Google-expanded occurrences exactly match the locked local expected sets.",
            "NO",
            "NO",
            "YES",
        )
    if any(frame.wrong_summary or frame.wrong_color for frame in frames):
        return (
            "E",
            "Google-expanded instances lost or changed summary/color ordering properties.",
            "PARTIAL",
            "NO",
            "UNKNOWN",
        )
    return (
        "C",
        "Stored parents match their payloads, but Google expansion differs from the local set.",
        "YES",
        "NO",
        "UNKNOWN",
    )


def _broken_parent_ids(frames: Sequence[FrameRemoteAudit]) -> list[str]:
    result: list[str] = []
    for frame in frames:
        for item in frame.first_divergences:
            if item.parent_id and item.parent_id not in result:
                result.append(item.parent_id)
    return result


def _representative_parent_ids(
    expected_by_frame: dict[int, list[ExpectedOccurrence]],
    selected_indices: Sequence[int],
) -> list[str]:
    result: list[str] = []
    for frame_index in selected_indices:
        occurrences = expected_by_frame[frame_index]
        if not occurrences:
            continue
        for position in (0, len(occurrences) // 2, len(occurrences) - 1):
            parent_id = occurrences[position].parent_id
            if parent_id not in result:
                result.append(parent_id)
    return result


def _account_b_frame_metadata(artifact: dict[str, object]) -> dict[int, dict[str, object]]:
    raw = artifact.get("frames")
    if not isinstance(raw, list):
        raise CalendarAnimError("Account-B animation artifact has invalid frames")
    result: dict[int, dict[str, object]] = {}
    for item in raw:
        if isinstance(item, dict):
            result[int(item["frame_index"])] = item
    return result


def _codepoints(value: str) -> list[str]:
    return [f"U+{ord(character):04X}" for character in value]


def _optional_string(value: object) -> str | None:
    return str(value) if value is not None else None


def _required_int(value: dict[str, object], key: str) -> int:
    raw = value.get(key)
    if not isinstance(raw, (int, str)):
        raise CalendarAnimError(f"Account-B animation artifact has invalid {key}")
    return int(raw)
