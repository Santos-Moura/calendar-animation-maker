import hashlib
import json

from calendar_anim.calendar.models import CalendarEventDraft


def deterministic_event_id(event: CalendarEventDraft) -> str:
    """Return a Google-compatible, stable ID for an immutable event draft."""

    canonical = {
        "start": event.start.isoformat(),
        "end": event.end.isoformat(),
        "color_id": event.color_id,
        "summary": event.summary,
        "private_metadata": dict(sorted(event.private_metadata.items())),
    }
    digest = hashlib.sha256(
        json.dumps(canonical, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return f"ca{digest}"
