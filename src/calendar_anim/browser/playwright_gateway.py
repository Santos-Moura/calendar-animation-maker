import hashlib
import json
import math
import os
import re
import shutil
import time
from dataclasses import dataclass
from datetime import date
from io import BytesIO
from pathlib import Path
from types import TracebackType
from typing import Any

from PIL import Image

from calendar_anim.calendar.capture.models import BrowserChannel, CalendarCaptureConfig
from calendar_anim.exceptions import CalendarAnimError

CALENDAR_HOME_URL = "https://calendar.google.com/calendar/u/0/r/week"
WEEK_PATH_PATTERN = re.compile(r"/week/(\d{4})/(\d{1,2})/(\d{1,2})(?:/|$)")
CALENDAR_REGION_SELECTORS = ("[role='main']", "main", "[role='grid']")
EVENT_SELECTORS = "[data-eventid], [data-eventchip], [data-dragsource-type='4']"
CALENDAR_ZOOM_HOST = "calendar.google.com"
NATIVE_BROWSER_ZOOM_LEVELS = (
    500,
    400,
    300,
    250,
    200,
    175,
    150,
    125,
    110,
    100,
    90,
    80,
    75,
    67,
    50,
    33,
    25,
)
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
  const requestedHours = options.endHour - options.startHour;
  const candidateScore = (element) => {
    const visibleHours = 24 * element.clientHeight / element.scrollHeight;
    const shortfall = Math.max(0, requestedHours - visibleHours - 0.1);
    const excess = Math.max(0, visibleHours - requestedHours);
    return shortfall * 1000 + excess;
  };
  candidates.sort((left, right) => {
    const scoreDifference = candidateScore(left) - candidateScore(right);
    if (Math.abs(scoreDifference) > 0.001) {
      return scoreDifference;
    }
    const leftRect = left.getBoundingClientRect();
    const rightRect = right.getBoundingClientRect();
    return (rightRect.width * rightRect.height) - (leftRect.width * leftRect.height);
  });
  const container = candidates[0];
  if (!container) {
    throw new Error('Could not find the Calendar vertical time scroller');
  }
  const pixelsPerHour = container.scrollHeight / 24;
  const desiredScrollTop = pixelsPerHour * options.startHour;
  const maximumScrollTop = Math.max(0, container.scrollHeight - container.clientHeight);
  const targetScrollTop = Math.min(desiredScrollTop, maximumScrollTop);
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
    desiredScrollTop,
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
    desired_scroll_top: float | None = None

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
            desired_scroll_top=raw.get("desiredScrollTop", raw["targetScrollTop"]),
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
    window = time_window_clip(metrics, start_hour, end_hour)
    if abs(window["y"] - metrics.container_y) > 3:
        return window
    header = week_header_clip(metrics)
    return {
        "x": window["x"],
        "y": header["y"],
        "width": window["width"],
        "height": window["y"] + window["height"] - header["y"],
    }


def time_window_clip(
    metrics: VisibleWindowMetrics,
    start_hour: int,
    end_hour: int,
) -> dict[str, float]:
    """Return the exact requested time window without the fixed week header."""

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
    visible_start_hour = metrics.scroll_top / pixels_per_hour
    start_offset = (start_hour - visible_start_hour) * pixels_per_hour
    if start_offset < -3:
        raise CalendarAnimError(f"Viewport does not include {start_hour:02d}:00")
    window_top = metrics.container_y + max(0, start_offset)
    clip_bottom = window_top + requested_height
    container_bottom = metrics.container_y + metrics.container_height
    if clip_bottom > container_bottom + 3:
        raise CalendarAnimError(
            f"Viewport cannot show {start_hour:02d}:00-{end_hour:02d}:00; "
            "increase viewport height or reduce the visible window"
        )
    region_bottom = metrics.region_y + metrics.region_height
    if clip_bottom > region_bottom + 3:
        raise CalendarAnimError("Requested Calendar time window exceeds the capture region")
    return {
        "x": metrics.region_x,
        "y": window_top,
        "width": metrics.region_width,
        "height": min(clip_bottom, region_bottom) - window_top,
    }


def week_header_clip(metrics: VisibleWindowMetrics) -> dict[str, float]:
    header_bottom = min(metrics.container_y, metrics.region_y + metrics.region_height)
    header_height = header_bottom - metrics.region_y
    if header_height <= 1:
        raise CalendarAnimError("Could not detect the Calendar week header above the time grid")
    return {
        "x": metrics.region_x,
        "y": metrics.region_y,
        "width": metrics.region_width,
        "height": header_height,
    }


def scale_clip_for_browser_zoom(
    clip: dict[str, float], applied_zoom_percent: float
) -> dict[str, float]:
    factor = applied_zoom_percent / 100
    if factor <= 0:
        raise CalendarAnimError("Applied browser zoom must be positive")
    return {key: value * factor for key, value in clip.items()}


def native_browser_zoom_factor(target_percent: int) -> float:
    if target_percent not in NATIVE_BROWSER_ZOOM_LEVELS:
        supported = ", ".join(f"{value}%" for value in NATIVE_BROWSER_ZOOM_LEVELS)
        raise CalendarAnimError(
            f"Unsupported native Chrome zoom {target_percent}%; supported: {supported}"
        )
    if target_percent == 33:
        return 1 / 3
    if target_percent == 67:
        return 2 / 3
    return target_percent / 100


def chromium_zoom_level(target_percent: int) -> float:
    return math.log(native_browser_zoom_factor(target_percent), 1.2)


def configure_calendar_zoom_preference(profile_directory: Path, target_percent: int) -> Path:
    """Persist native per-host zoom before Chrome opens."""

    preferences_path = profile_directory / "Default" / "Preferences"
    if not preferences_path.is_file():
        raise CalendarAnimError(
            f"Chrome profile preferences do not exist: {preferences_path}. "
            "Run calendar browser-login first."
        )
    try:
        preferences = json.loads(preferences_path.read_text(encoding="utf-8"))
        zoom_hosts = (
            preferences.setdefault("partition", {})
            .setdefault("per_host_zoom_levels", {})
            .setdefault("x", {})
        )
        zoom_level = chromium_zoom_level(target_percent)
        current = zoom_hosts.get(CALENDAR_ZOOM_HOST, {})
        changed = abs(float(current.get("zoom_level", 1000)) - zoom_level) > 1e-9
        if not changed:
            return preferences_path
        zoom_hosts[CALENDAR_ZOOM_HOST] = {
            "last_modified": str(round((time.time() + 11_644_473_600) * 1_000_000)),
            "zoom_level": zoom_level,
        }
        shutil.copy2(preferences_path, preferences_path.with_name("Preferences.calendar-anim.bak"))
        temporary = preferences_path.with_name("Preferences.calendar-anim.tmp")
        temporary.write_text(
            json.dumps(preferences, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
        os.replace(temporary, preferences_path)
    except (json.JSONDecodeError, OSError, TypeError, ValueError) as error:
        raise CalendarAnimError(
            f"Could not configure native Chrome zoom in {preferences_path}"
        ) from error
    return preferences_path


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
        self._header_clip: dict[str, float] | None = None
        self._time_window_clip: dict[str, float] | None = None
        self._applied_zoom_percent: float | None = None

    def __enter__(self) -> "PlaywrightCalendarCaptureGateway":
        try:
            from playwright.sync_api import sync_playwright
        except ModuleNotFoundError as error:
            raise CalendarAnimError(
                "Playwright is not installed. Install the project dependencies first."
            ) from error
        self.config.profile_directory.mkdir(parents=True, exist_ok=True)
        try:
            if self.config.browser_zoom_percent != 100:
                configure_calendar_zoom_preference(
                    self.config.profile_directory, self.config.browser_zoom_percent
                )
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
        self._header_clip = None
        self._time_window_clip = None
        self._applied_zoom_percent = None

    def open_week(self, week_start: date) -> None:
        page = self._require_page()
        page.goto(calendar_week_url(week_start), wait_until="domcontentloaded")
        if self.config.browser_zoom_percent == 100:
            page.keyboard.press("Control+0")
            self._applied_zoom_percent = 100.0
        else:
            page.bring_to_front()
            self._applied_zoom_percent = self._verify_browser_zoom(
                self.config.browser_zoom_percent
            )
        self._capture_clip = None
        self._header_clip = None
        self._time_window_clip = None

    def _verify_browser_zoom(self, target_percent: int) -> float:
        page = self._require_page()
        page.wait_for_timeout(350)
        target_factor = native_browser_zoom_factor(target_percent)
        current_dpr = float(page.evaluate("window.devicePixelRatio"))
        applied_factor = current_dpr / self.config.device_scale_factor
        applied_percent = applied_factor * 100
        if abs(applied_factor - target_factor) <= 0.015:
            return applied_percent
        raise CalendarAnimError(
            f"Chrome did not apply requested zoom {target_percent}%; "
            f"measured approximately {applied_percent:.1f}%. "
            "Close every Chrome window using the calendar-animation profile and retry."
        )

    def wait_until_ready(self, week_start: date, minimum_event_count: int) -> None:
        page = self._require_page()
        page.wait_for_load_state("domcontentloaded")
        self._validate_week_url(page.url, week_start)
        region = self._find_visible_capture_region()
        region.wait_for(state="visible")
        if minimum_event_count > 0:
            page.locator(EVENT_SELECTORS).first.wait_for(state="visible")
        header_clip, time_clip = self._position_visible_window(region)
        self._wait_for_stable_snapshots(time_clip)
        self._validate_week_url(page.url, week_start)
        self._header_clip = header_clip
        self._time_window_clip = time_clip
        self._capture_clip = time_clip

    def capture(self, output_path: Path) -> None:
        if self._capture_clip is None:
            raise CalendarAnimError("Calendar region is not ready for capture")
        if self._header_clip is None or self._time_window_clip is None:
            raise CalendarAnimError("Calendar header and time window are not ready for capture")
        page = self._require_page()
        header_bytes = page.screenshot(
            animations="disabled", scale="css", clip=self._header_clip
        )
        window_bytes = page.screenshot(
            animations="disabled", scale="css", clip=self._time_window_clip
        )
        header: Image.Image | None = None
        window: Image.Image | None = None
        composed: Image.Image | None = None
        try:
            with Image.open(BytesIO(header_bytes)) as header_source:
                header = header_source.convert("RGB")
            with Image.open(BytesIO(window_bytes)) as window_source:
                window = window_source.convert("RGB")
            if header.width != window.width:
                raise CalendarAnimError("Calendar header and time window widths do not match")
            composed = Image.new("RGB", (header.width, header.height + window.height))
            composed.paste(header, (0, 0))
            composed.paste(window, (0, header.height))
            output_path.parent.mkdir(parents=True, exist_ok=True)
            composed.save(output_path)
        finally:
            for image in (header, window, composed):
                if image is not None:
                    image.close()

    def _find_visible_capture_region(self) -> Any:
        page = self._require_page()
        for selector in CALENDAR_REGION_SELECTORS:
            candidates = page.locator(selector)
            for index in range(candidates.count()):
                candidate = candidates.nth(index)
                if candidate.is_visible():
                    return candidate
        raise CalendarAnimError("Could not find a visible Google Calendar week grid")

    def _position_visible_window(
        self, region: Any
    ) -> tuple[dict[str, float], dict[str, float]]:
        raw = region.evaluate(
            POSITION_VISIBLE_WINDOW_SCRIPT,
            {
                "startHour": self.config.visible_start_hour,
                "endHour": self.config.visible_end_hour,
            },
        )
        metrics = VisibleWindowMetrics.from_browser(raw)
        if self._applied_zoom_percent is None:
            raise CalendarAnimError("Browser zoom was not applied before capture positioning")
        header = week_header_clip(metrics)
        window = time_window_clip(
            metrics,
            self.config.visible_start_hour,
            self.config.visible_end_hour,
        )
        return (
            scale_clip_for_browser_zoom(header, self._applied_zoom_percent),
            scale_clip_for_browser_zoom(window, self._applied_zoom_percent),
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
