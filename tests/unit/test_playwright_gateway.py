from datetime import date

import pytest

from calendar_anim.browser.playwright_gateway import (
    PlaywrightCalendarCaptureGateway,
    calendar_week_url,
)
from calendar_anim.exceptions import CalendarAnimError

pytestmark = pytest.mark.unit


def test_calendar_week_url_and_validation_use_exact_persisted_week() -> None:
    week = date(2026, 10, 4)

    assert calendar_week_url(week).endswith("/week/2026/10/4")
    PlaywrightCalendarCaptureGateway._validate_week_url(
        "https://calendar.google.com/calendar/u/0/r/week/2026/10/4", week
    )

    with pytest.raises(CalendarAnimError, match="expected persisted week"):
        PlaywrightCalendarCaptureGateway._validate_week_url(
            "https://calendar.google.com/calendar/u/0/r/week/2026/10/11", week
        )


def test_validation_rejects_login_or_non_week_pages() -> None:
    with pytest.raises(CalendarAnimError, match="manual login may be required"):
        PlaywrightCalendarCaptureGateway._validate_week_url(
            "https://accounts.google.com/signin", date(2026, 10, 4)
        )
