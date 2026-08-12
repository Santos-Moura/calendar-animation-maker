import hashlib
import json
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from calendar_anim.calendar.frame_mapping.models import SingleFrameCalendarPlan
from calendar_anim.calendar.multi_frame.artifacts import AnimationRunStore
from calendar_anim.calendar.recurrence_validation.models import (
    RecurrenceValidationPlan,
    ValidationResourcePlan,
    ValidationResourceRole,
    ValidationVisualProperties,
    ValidationWeek,
)
from calendar_anim.exceptions import CalendarAnimError


def _event_id(validation_id: str, role: str, start: datetime) -> str:
    canonical = json.dumps(
        {"validation_id": validation_id, "role": role, "start": start.isoformat()},
        sort_keys=True,
        separators=(",", ":"),
    )
    return "rv" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _at_validation_week(
    source: datetime,
    source_week: date,
    target_week: date,
    timezone: str,
) -> datetime:
    day_offset = (source.date() - source_week).days
    if not 0 <= day_offset <= 6:
        raise CalendarAnimError("source event falls outside its frame week")
    local_time = time(source.hour, source.minute, source.second, source.microsecond)
    return datetime.combine(
        target_week + timedelta(days=day_offset), local_time, ZoneInfo(timezone)
    )


def _rdate_line(timezone: str, starts: list[datetime]) -> str:
    values = ",".join(start.strftime("%Y%m%dT%H%M%S") for start in starts)
    return f"RDATE;TZID={timezone}:{values}"


def build_recurrence_validation_plan(
    store: AnimationRunStore,
    *,
    validation_id: str,
    source_run_id: str,
    source_frame_index: int,
    source_event_index: int,
    start_week: date,
) -> RecurrenceValidationPlan:
    animation_plan = store.load_plan(source_run_id)
    source_plan: SingleFrameCalendarPlan = store.load_frame_plan(animation_plan, source_frame_index)
    if source_event_index >= len(source_plan.events):
        raise CalendarAnimError(
            f"Source frame {source_frame_index} has no event {source_event_index}"
        )
    if start_week.weekday() != 6:
        raise CalendarAnimError("--start-week must be a Sunday")
    source = source_plan.events[source_event_index]
    if source.summary not in source_plan.subcolumn_order_keys:
        raise CalendarAnimError("source event does not use the frame's zero-width summary keys")
    if source.color_id is None:
        raise CalendarAnimError("source event has no Calendar colorId")
    duration = source.end - source.start
    recurring_weeks = [start_week + timedelta(weeks=offset) for offset in (0, 2, 4)]
    standalone_weeks = [start_week + timedelta(weeks=offset) for offset in (1, 3, 5)]
    recurring_starts = [
        _at_validation_week(
            source.start,
            source_plan.week_start_date,
            week,
            source_plan.timezone,
        )
        for week in recurring_weeks
    ]
    visual_metadata = {
        "generated_by": "calendar-anim-recurrence-validation",
        "validation_id": validation_id,
        "source_run_id": source_run_id,
        "source_frame_index": str(source_frame_index),
        "source_event_index": str(source_event_index),
        "subcolumn_order_strategy": "zero-width",
    }
    parent_start = recurring_starts[0]
    resources = [
        ValidationResourcePlan(
            event_id=_event_id(validation_id, "recurring-parent", parent_start),
            role=ValidationResourceRole.RECURRING_PARENT,
            week_start=recurring_weeks[0],
            start=parent_start,
            end=parent_start + duration,
            summary=source.summary,
            color_id=source.color_id,
            timezone=source_plan.timezone,
            recurrence=[_rdate_line(source_plan.timezone, recurring_starts[1:])],
            private_metadata={**visual_metadata, "validation_role": "recurring-parent"},
        )
    ]
    for pair_index, week in enumerate(standalone_weeks):
        start = _at_validation_week(
            source.start,
            source_plan.week_start_date,
            week,
            source_plan.timezone,
        )
        resources.append(
            ValidationResourcePlan(
                event_id=_event_id(validation_id, f"standalone-{pair_index}", start),
                role=ValidationResourceRole.STANDALONE_CONTROL,
                pair_index=pair_index,
                week_start=week,
                start=start,
                end=start + duration,
                summary=source.summary,
                color_id=source.color_id,
                timezone=source_plan.timezone,
                private_metadata={
                    **visual_metadata,
                    "validation_role": "standalone-control",
                    "pair_index": str(pair_index),
                },
            )
        )
    weeks = [
        ValidationWeek(pair_index=index, variant="recurring", week_start=recurring_weeks[index])
        for index in range(3)
    ] + [
        ValidationWeek(pair_index=index, variant="standalone", week_start=standalone_weeks[index])
        for index in range(3)
    ]
    weeks.sort(key=lambda item: item.week_start)
    codepoints = " ".join(f"U+{ord(character):04X}" for character in source.summary)
    return RecurrenceValidationPlan(
        validation_id=validation_id,
        source_run_id=source_run_id,
        source_frame_index=source_frame_index,
        source_event_index=source_event_index,
        calendar_name=source_plan.calendar_name,
        timezone=source_plan.timezone,
        visual_properties=ValidationVisualProperties(
            summary=source.summary,
            summary_codepoints=codepoints,
            color_id=source.color_id,
            local_start_time=source.start.strftime("%H:%M:%S"),
            duration_seconds=int(duration.total_seconds()),
            timezone=source_plan.timezone,
        ),
        weeks=weeks,
        resources=resources,
    )
