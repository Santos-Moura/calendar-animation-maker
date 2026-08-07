from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from calendar_anim.calendar.models import CalendarEventDraft, CalendarPlan
from calendar_anim.exceptions import CalendarAnimError
from calendar_anim.models.animation import AnimationManifest


def plan_events(manifest: AnimationManifest, start_date: date, timezone: str) -> CalendarPlan:
    try:
        zone = ZoneInfo(timezone)
    except ZoneInfoNotFoundError as error:
        raise CalendarAnimError(f"Unknown timezone: {timezone}") from error
    events: list[CalendarEventDraft] = []
    for frame in manifest.frames:
        week = start_date + timedelta(weeks=frame.index)
        for block_index, block in enumerate(frame.blocks):
            day = week + timedelta(days=min(block.x * 7 // manifest.render.grid_width, 6))
            minute = block.y * 1440 // manifest.render.grid_height
            start = datetime.combine(day, time.min, zone) + timedelta(minutes=minute)
            end = start + timedelta(minutes=max(1, 1440 // manifest.render.grid_height))
            events.append(
                CalendarEventDraft(
                    frame_index=frame.index,
                    block_index=block_index,
                    start=start,
                    end=end,
                    color_id=block.color_id,
                    summary=f"calendar-anim:{manifest.animation_id}",
                    private_metadata={
                        "animation_id": manifest.animation_id,
                        "frame_index": str(frame.index),
                        "block_index": str(block_index),
                    },
                )
            )
    return CalendarPlan(animation_id=manifest.animation_id, timezone=timezone, events=events)
