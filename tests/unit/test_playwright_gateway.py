import json
from datetime import date
from pathlib import Path

import pytest

from calendar_anim.browser.playwright_gateway import (
    PlaywrightCalendarCaptureGateway,
    VisibleWindowMetrics,
    calendar_week_url,
    capture_clip_for_window,
    chromium_zoom_level,
    configure_calendar_zoom_preference,
    native_browser_zoom_factor,
    scale_clip_for_browser_zoom,
    time_window_clip,
    week_header_clip,
)
from calendar_anim.calendar.capture.commands import _capture_config
from calendar_anim.calendar.capture.models import BrowserChannel, CaptureProfile
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


def test_high_detail_window_reaches_midnight_and_preserves_week_header() -> None:
    metrics = VisibleWindowMetrics(
        region_x=72,
        region_y=40,
        region_width=1848,
        region_height=1200,
        container_y=100,
        container_height=1140,
        scroll_height=1440,
        scroll_top=300,
        target_scroll_top=300,
        desired_scroll_top=360,
    )

    assert week_header_clip(metrics) == {"x": 72, "y": 40, "width": 1848, "height": 60}
    assert time_window_clip(metrics, 6, 24) == {
        "x": 72,
        "y": 160,
        "width": 1848,
        "height": 1080,
    }


def test_high_detail_capture_profile_is_explicit_and_production_is_unchanged() -> None:
    production = _capture_config(
        Path("profile"), 2, 30, BrowserChannel.CHROME, CaptureProfile.PRODUCTION
    )
    high_detail = _capture_config(
        Path("profile"), 2, 30, BrowserChannel.CHROME, CaptureProfile.HIGH_DETAIL_126X72
    )

    assert (production.browser_zoom_percent, production.visible_end_hour) == (100, 18)
    assert (high_detail.browser_zoom_percent, high_detail.visible_end_hour) == (33, 24)
    assert high_detail.visible_start_hour == 6
    assert high_detail.week_header_visible is True
    assert high_detail.color_scheme == "dark"


def test_native_zoom_persists_exact_thirty_three_percent(tmp_path: Path) -> None:
    default = tmp_path / "Default"
    default.mkdir()
    preferences = default / "Preferences"
    preferences.write_text("{}", encoding="utf-8")

    configure_calendar_zoom_preference(tmp_path, 33)

    saved = json.loads(preferences.read_text(encoding="utf-8"))
    zoom = saved["partition"]["per_host_zoom_levels"]["x"]["calendar.google.com"]
    assert native_browser_zoom_factor(33) == pytest.approx(1 / 3)
    assert zoom["zoom_level"] == pytest.approx(chromium_zoom_level(33))
    assert scale_clip_for_browser_zoom(
        {"x": 0, "y": 300, "width": 1800, "height": 900}, 100 / 3
    ) == pytest.approx({"x": 0, "y": 100, "width": 600, "height": 300})


def test_native_zoom_persists_account_b_ninety_percent(tmp_path: Path) -> None:
    default = tmp_path / "Default"
    default.mkdir()
    preferences = default / "Preferences"
    preferences.write_text("{}", encoding="utf-8")

    configure_calendar_zoom_preference(tmp_path, 90)

    saved = json.loads(preferences.read_text(encoding="utf-8"))
    zoom = saved["partition"]["per_host_zoom_levels"]["x"]["calendar.google.com"]
    assert native_browser_zoom_factor(90) == pytest.approx(0.9)
    assert zoom["zoom_level"] == pytest.approx(chromium_zoom_level(90))
