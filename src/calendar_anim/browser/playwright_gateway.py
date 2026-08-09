import hashlib
import re
import time
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from types import TracebackType
from typing import Any

from calendar_anim.calendar.capture.models import BrowserChannel, CalendarCaptureConfig
from calendar_anim.exceptions import CalendarAnimError

CALENDAR_HOME_URL = "https://calendar.google.com/calendar/u/0/r/week"
WEEK_PATH_PATTERN = re.compile(r"/week/(\d{4})/(\d{1,2})/(\d{1,2})(?:/|$)")
CALENDAR_REGION_SELECTORS = ("[role='main']", "main", "[role='grid']")
EVENT_SELECTORS = "[data-eventid], [data-eventchip], [data-dragsource-type='4']"
POSITION_VISIBLE_WINDOW_SCRIPT = """
async (root, options) => {
  const rootRect = root.getBoundingClientRect();
  const candidates = [root, ...root.querySelectorAll('*')].filter((element) => {
    const rect = element.getBoundingClientRect();
    const style = window.getComputedStyle(element);
    const scrollable = element.scrollHeight > element.clientHeight + 100;
    const wideEnough = rect.width >= rootRect.width * 0.6;
    const tallEnough = rect.height >= 300;
    const visible = rect.bottom > rootRect.top && rect.top < rootRect.bottom;
    const permitsScroll = ['auto', 'scroll'].includes(style.overflowY) || scrollable;
    return scrollable && wideEnough && tallEnough && visible && permitsScroll;
  });
  candidates.sort((left, right) => {
    const leftRect = left.getBoundingClientRect();
    const rightRect = right.getBoundingClientRect();
    return (rightRect.width * rightRect.height) - (leftRect.width * leftRect.height);
  });
  const container = candidates[0];
  if (!container) {
    throw new Error('Could not find the Calendar vertical time scroller');
  }
  const pixelsPerHour = container.scrollHeight / 24;
  const targetScrollTop = pixelsPerHour * options.startHour;
  container.scrollTop = targetScrollTop;
  container.dispatchEvent(new Event('scroll', { bubbles: true }));
  await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));
  const containerRect = container.getBoundingClientRect();
  return {
    regionX: rootRect.x,
    regionY: rootRect.y,
    regionWidth: rootRect.width,
    regionHeight: rootRect.height,
    containerY: containerRect.y,
    containerHeight: container.clientHeight,
    scrollHeight: container.scrollHeight,
    scrollTop: container.scrollTop,
    targetScrollTop,
  };
}
"""


@dataclass(frozen=True)
class VisibleWindowMetrics:
    region_x: float
    region_y: float
    region_width: float
    region_height: float
    container_y: float
    container_height: float
    scroll_height: float
    scroll_top: float
    target_scroll_top: float

    @classmethod
    def from_browser(cls, raw: dict[str, float]) -> "VisibleWindowMetrics":
        return cls(
            region_x=raw["regionX"],
            region_y=raw["regionY"],
            region_width=raw["regionWidth"],
            region_height=raw["regionHeight"],
            container_y=raw["containerY"],
            container_height=raw["containerHeight"],
            scroll_height=raw["scrollHeight"],
            scroll_top=raw["scrollTop"],
            target_scroll_top=raw["targetScrollTop"],
        )


def capture_clip_for_window(
    metrics: VisibleWindowMetrics,
    start_hour: int,
    end_hour: int,
) -> dict[str, float]:
    if metrics.scroll_height <= 0 or metrics.container_height <= 0:
        raise CalendarAnimError("Calendar time-grid geometry is invalid")
    if abs(metrics.scroll_top - metrics.target_scroll_top) > 3:
        raise CalendarAnimError(
            f"Calendar did not scroll to {start_hour:02d}:00 "
            f"({metrics.scroll_top:.1f}px != {metrics.target_scroll_top:.1f}px)"
        )
    pixels_per_hour = metrics.scroll_height / 24
    requested_height = (end_hour - start_hour) * pixels_per_hour
    if requested_height > metrics.container_height + 3:
        raise CalendarAnimError(
            f"Viewport cannot show {start_hour:02d}:00-{end_hour:02d}:00; "
            "increase viewport height or reduce the visible window"
        )
    clip_bottom = metrics.container_y + requested_height
    region_bottom = metrics.region_y + metrics.region_height
    if clip_bottom > region_bottom + 3:
        raise CalendarAnimError("Requested Calendar time window exceeds the capture region")
    return {
        "x": metrics.region_x,
        "y": metrics.region_y,
        "width": metrics.region_width,
        "height": min(clip_bottom, region_bottom) - metrics.region_y,
    }


def calendar_week_url(week_start: date) -> str:
    return f"{CALENDAR_HOME_URL}/{week_start.year}/{week_start.month}/{week_start.day}"


class PlaywrightCalendarCaptureGateway:
    """Headed Calendar capture using a dedicated, manually authenticated profile."""

    def __init__(self, config: CalendarCaptureConfig) -> None:
        self.config = config
        self._playwright: Any | None = None
        self._context: Any | None = None
        self._page: Any | None = None
        self._capture_clip: dict[str, float] | None = None

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
            channel = (
                None
                if self.config.browser_channel is BrowserChannel.BUNDLED_CHROMIUM
                else self.config.browser_channel.value
            )
            self._context = self._playwright.chromium.launch_persistent_context(
                user_data_dir=self.config.profile_directory,
                channel=channel,
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
        self._capture_clip = None

    def open_week(self, week_start: date) -> None:
        page = self._require_page()
        page.goto(calendar_week_url(week_start), wait_until="domcontentloaded")
        if self.config.browser_zoom_percent == 100:
            page.keyboard.press("Control+0")
        else:
            raise CalendarAnimError("Only the calibrated 100% browser zoom is currently supported")
        self._capture_clip = None

    def wait_until_ready(self, week_start: date, minimum_event_count: int) -> None:
        page = self._require_page()
        page.wait_for_load_state("domcontentloaded")
        self._validate_week_url(page.url, week_start)
        region = self._find_visible_capture_region()
        region.wait_for(state="visible")
        if minimum_event_count > 0:
            page.locator(EVENT_SELECTORS).first.wait_for(state="visible")
        clip = self._position_visible_window(region)
        self._wait_for_stable_snapshots(clip)
        self._validate_week_url(page.url, week_start)
        self._capture_clip = clip

    def capture(self, output_path: Path) -> None:
        if self._capture_clip is None:
            raise CalendarAnimError("Calendar region is not ready for capture")
        self._require_page().screenshot(
            path=output_path,
            animations="disabled",
            scale="css",
            clip=self._capture_clip,
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

    def _position_visible_window(self, region: Any) -> dict[str, float]:
        raw = region.evaluate(
            POSITION_VISIBLE_WINDOW_SCRIPT,
            {
                "startHour": self.config.visible_start_hour,
                "endHour": self.config.visible_end_hour,
            },
        )
        metrics = VisibleWindowMetrics.from_browser(raw)
        return capture_clip_for_window(
            metrics,
            self.config.visible_start_hour,
            self.config.visible_end_hour,
        )

    def _wait_for_stable_snapshots(self, clip: dict[str, float]) -> None:
        deadline = time.monotonic() + self.config.ready_timeout_seconds
        stable = 0
        previous: str | None = None
        while time.monotonic() <= deadline:
            snapshot = self._require_page().screenshot(
                animations="disabled", scale="css", clip=clip
            )
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
