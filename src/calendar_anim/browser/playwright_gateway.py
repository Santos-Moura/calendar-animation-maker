import hashlib
import re
import time
from datetime import date
from pathlib import Path
from types import TracebackType
from typing import Any

from calendar_anim.calendar.capture.models import CalendarCaptureConfig
from calendar_anim.exceptions import CalendarAnimError

CALENDAR_HOME_URL = "https://calendar.google.com/calendar/u/0/r/week"
WEEK_PATH_PATTERN = re.compile(r"/week/(\d{4})/(\d{1,2})/(\d{1,2})(?:/|$)")
CALENDAR_REGION_SELECTORS = ("[role='main']", "main", "[role='grid']")
EVENT_SELECTORS = "[data-eventid], [data-eventchip], [data-dragsource-type='4']"


def calendar_week_url(week_start: date) -> str:
    return f"{CALENDAR_HOME_URL}/{week_start.year}/{week_start.month}/{week_start.day}"


class PlaywrightCalendarCaptureGateway:
    """Headed Calendar capture using a dedicated, manually authenticated profile."""

    def __init__(self, config: CalendarCaptureConfig) -> None:
        self.config = config
        self._playwright: Any | None = None
        self._context: Any | None = None
        self._page: Any | None = None
        self._capture_region: Any | None = None

    def __enter__(self) -> "PlaywrightCalendarCaptureGateway":
        try:
            from playwright.sync_api import sync_playwright
        except ModuleNotFoundError as error:
            raise CalendarAnimError(
                "Playwright is not installed. Install the project dependencies first."
            ) from error
        self.config.profile_directory.mkdir(parents=True, exist_ok=True)
        try:
            self._playwright = sync_playwright().start()
            self._context = self._playwright.chromium.launch_persistent_context(
                user_data_dir=self.config.profile_directory,
                headless=False,
                viewport={
                    "width": self.config.viewport_width,
                    "height": self.config.viewport_height,
                },
                device_scale_factor=self.config.device_scale_factor,
                color_scheme=self.config.color_scheme,
                accept_downloads=False,
            )
            self._context.set_default_timeout(self.config.ready_timeout_seconds * 1000)
            self._page = self._context.pages[0] if self._context.pages else self._context.new_page()
        except Exception:
            self.close()
            raise
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        if self._context is not None:
            self._context.close()
            self._context = None
        if self._playwright is not None:
            self._playwright.stop()
            self._playwright = None
        self._page = None
        self._capture_region = None

    def open_for_manual_login(self) -> None:
        page = self._require_page()
        page.goto(CALENDAR_HOME_URL, wait_until="domcontentloaded")

    def open_week(self, week_start: date) -> None:
        page = self._require_page()
        page.goto(calendar_week_url(week_start), wait_until="domcontentloaded")
        if self.config.browser_zoom_percent == 100:
            page.keyboard.press("Control+0")
        else:
            raise CalendarAnimError("Only the calibrated 100% browser zoom is currently supported")
        self._capture_region = None

    def wait_until_ready(self, week_start: date, minimum_event_count: int) -> None:
        page = self._require_page()
        page.wait_for_load_state("domcontentloaded")
        self._validate_week_url(page.url, week_start)
        region = self._find_visible_capture_region()
        region.wait_for(state="visible")
        if minimum_event_count > 0:
            page.locator(EVENT_SELECTORS).first.wait_for(state="visible")
        self._wait_for_stable_snapshots(region)
        self._validate_week_url(page.url, week_start)
        self._capture_region = region

    def capture(self, output_path: Path) -> None:
        if self._capture_region is None:
            raise CalendarAnimError("Calendar region is not ready for capture")
        self._capture_region.screenshot(
            path=output_path,
            animations="disabled",
            scale="css",
        )

    def _find_visible_capture_region(self) -> Any:
        page = self._require_page()
        for selector in CALENDAR_REGION_SELECTORS:
            candidates = page.locator(selector)
            for index in range(candidates.count()):
                candidate = candidates.nth(index)
                if candidate.is_visible():
                    return candidate
        raise CalendarAnimError("Could not find a visible Google Calendar week grid")

    def _wait_for_stable_snapshots(self, region: Any) -> None:
        deadline = time.monotonic() + self.config.ready_timeout_seconds
        stable = 0
        previous: str | None = None
        while time.monotonic() <= deadline:
            snapshot = region.screenshot(animations="disabled", scale="css")
            digest = hashlib.sha256(snapshot).hexdigest()
            stable = stable + 1 if digest == previous else 1
            if stable >= self.config.stable_snapshot_count:
                return
            previous = digest
            time.sleep(self.config.stabilization_seconds)
        raise CalendarAnimError("Google Calendar week grid did not stabilize before timeout")

    @staticmethod
    def _validate_week_url(url: str, expected: date) -> None:
        match = WEEK_PATH_PATTERN.search(url)
        if match is None:
            raise CalendarAnimError(
                "Google Calendar did not remain in a directly addressable week view; "
                "manual login may be required"
            )
        actual = date(*(int(part) for part in match.groups()))
        if actual != expected:
            raise CalendarAnimError(
                f"Calendar opened week {actual}, expected persisted week {expected}"
            )

    def _require_page(self) -> Any:
        if self._page is None:
            raise CalendarAnimError("Playwright browser context is not open")
        return self._page
