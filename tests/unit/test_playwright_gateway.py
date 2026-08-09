from datetime import date

import pytest

from calendar_anim.browser.playwright_gateway import (
    PlaywrightCalendarCaptureGateway,
    VisibleWindowMetrics,
    calendar_week_url,
    capture_clip_for_window,
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


def test_capture_clip_aligns_six_to_eighteen_and_keeps_calendar_header() -> None:
    metrics = VisibleWindowMetrics(
        region_x=72,
        region_y=50,
        region_width=1848,
        region_height=1000,
        container_y=150,
        container_height=720,
        scroll_height=1440,
        scroll_top=360,
        target_scroll_top=360,
    )

    clip = capture_clip_for_window(metrics, 6, 18)

    assert clip == {"x": 72, "y": 50, "width": 1848, "height": 820}


def test_capture_clip_rejects_wrong_scroll_or_insufficient_viewport() -> None:
    wrong_scroll = VisibleWindowMetrics(0, 0, 1800, 1000, 100, 720, 1440, 660, 360)
    too_short = VisibleWindowMetrics(0, 0, 1800, 1000, 100, 600, 1440, 360, 360)

    with pytest.raises(CalendarAnimError, match="did not scroll to 06:00"):
        capture_clip_for_window(wrong_scroll, 6, 18)
    with pytest.raises(CalendarAnimError, match="cannot show 06:00-18:00"):
        capture_clip_for_window(too_short, 6, 18)
