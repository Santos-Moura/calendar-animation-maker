from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from calendar_anim.calendar.cayde_216.models import Cayde216WindowCandidate
from calendar_anim.calendar.models import CalendarRangeEvent


def find_clean_windows(
    events: list[CalendarRangeEvent],
    *,
    search_start: date,
    search_end_exclusive: date,
    timezone: str,
    count: int = 2,
) -> list[Cayde216WindowCandidate]:
    """Find distinct, non-overlapping, event-free 216-week windows."""

    zone = ZoneInfo(timezone)
    candidates: list[Cayde216WindowCandidate] = []
    candidate_start = search_start
    while candidate_start + timedelta(weeks=216) <= search_end_exclusive:
        candidate_end = candidate_start + timedelta(weeks=216)
        start_at = datetime.combine(candidate_start, time.min, zone)
        end_at = datetime.combine(candidate_end, time.min, zone)
        conflicts = sum(event.start < end_at and event.end > start_at for event in events)
        if conflicts == 0:
            candidates.append(
                Cayde216WindowCandidate(
                    rank=len(candidates) + 1,
                    first_week=candidate_start,
                    last_week=candidate_start + timedelta(weeks=215),
                    end_exclusive=candidate_end,
                )
            )
            if len(candidates) == count:
                return candidates
            candidate_start = candidate_end
        else:
            candidate_start += timedelta(weeks=1)
    return candidates
