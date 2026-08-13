import hashlib
import json
import math
import os
import re
import shutil
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta
from io import BytesIO
from pathlib import Path
from types import TracebackType
from typing import Any

from PIL import Image

from calendar_anim.calendar.capture.models import BrowserChannel, CalendarCaptureConfig
from calendar_anim.exceptions import CalendarAnimError

CALENDAR_ROOT_URL = "https://calendar.google.com/calendar/u/0/r"
CALENDAR_HOME_URL = f"{CALENDAR_ROOT_URL}/week"
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
ANIMATION_EVENT_AUDIT_SCRIPT = """
(elements) => {
  const nodes = Array.from(elements);
  const eventSelector = "[data-eventid], [data-eventchip], [data-dragsource-type='4']";
  const transparent = new Set(['', 'transparent', 'rgba(0, 0, 0, 0)']);
  const opaque = (value) => !transparent.has(value || '');
  return nodes.map((element, index) => {
    const rect = element.getBoundingClientRect();
    const matchingDescendant = element.querySelector(eventSelector);
    const containsMatchingDescendant = matchingDescendant !== null;
    const style = window.getComputedStyle(element);
    const visibleColors = [
      style.backgroundColor,
      window.getComputedStyle(element, '::before').backgroundColor,
      window.getComputedStyle(element, '::after').backgroundColor,
      ...Array.from(containsMatchingDescendant ? [] :
        element.querySelectorAll('*')).flatMap((child) => {
        const childStyle = window.getComputedStyle(child);
        return [childStyle.backgroundColor,
          window.getComputedStyle(child, '::before').backgroundColor,
          window.getComputedStyle(child, '::after').backgroundColor];
      }),
    ].filter(opaque);
    const link = element.closest('a[href]') || element.querySelector('a[href]');
    return {
      raw_index: index,
      contains_matching_descendant: containsMatchingDescendant,
      matching_descendant_count: containsMatchingDescendant ? 1 : 0,
      data_eventid: element.getAttribute('data-eventid'),
      data_eventchip: element.getAttribute('data-eventchip'),
      data_dragsource_type: element.getAttribute('data-dragsource-type'),
      href: link ? link.getAttribute('href') : null,
      aria_label: element.getAttribute('aria-label') || '',
      text: (element.textContent || '').trim(),
      visible_color: visibleColors[0] || null,
      x: rect.x,
      y: rect.y,
      width: rect.width,
      height: rect.height,
      viewport_width: window.innerWidth,
      viewport_height: window.innerHeight,
      in_viewport: rect.right > 0 && rect.bottom > 0 &&
        rect.left < window.innerWidth && rect.top < window.innerHeight,
    };
  });
}
"""
STRUCTURAL_WEEK_GRID_SCRIPT = r"""
(root) => {
  const rootRect = root.getBoundingClientRect();
  const eventSelector = "[data-eventid], [data-eventchip], [data-dragsource-type='4']";
  const describe = (element) => {
    const rect = element.getBoundingClientRect();
    const parent = element.parentElement;
    const style = window.getComputedStyle(element);
    const children = Array.from(element.children).slice(0, 12).map((child) => {
      const childRect = child.getBoundingClientRect();
      return {
        tag: child.tagName.toLowerCase(),
        role: child.getAttribute('role'),
        class_name: typeof child.className === 'string' ? child.className : '',
        left: childRect.left, right: childRect.right,
        width: childRect.width, height: childRect.height,
      };
    });
    return {
      tag: element.tagName.toLowerCase(),
      role: element.getAttribute('role'),
      aria_label: element.getAttribute('aria-label'),
      class_name: typeof element.className === 'string' ? element.className : '',
      data_attributes: Object.fromEntries(Array.from(element.attributes)
        .filter((attribute) => attribute.name.startsWith('data-'))
        .slice(0, 12).map((attribute) => [attribute.name, attribute.value])),
      parent: parent ? {
        tag: parent.tagName.toLowerCase(),
        role: parent.getAttribute('role'),
        class_name: typeof parent.className === 'string' ? parent.className : '',
      } : null,
      child_count: element.children.length,
      children,
      left: rect.left, right: rect.right, top: rect.top, bottom: rect.bottom,
      width: rect.width, height: rect.height,
      display: style.display,
      grid_template_columns: style.gridTemplateColumns,
    };
  };
  const structural = [root, ...root.querySelectorAll('*')].filter((element) => {
    if (element.matches(eventSelector) || element.closest(eventSelector)) return false;
    const rect = element.getBoundingClientRect();
    const style = window.getComputedStyle(element);
    return rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden' &&
      style.display !== 'none' && rect.right > rootRect.left && rect.left < rootRect.right &&
      rect.bottom > rootRect.top && rect.top < rootRect.bottom;
  });
  const described = structural.map(describe);
  const cssGridCandidates = described.filter((item) => {
    const tracks = item.grid_template_columns.split(/\s+/).filter(Boolean)
      .map((value) => Number.parseFloat(value));
    if (tracks.length !== 7 || tracks.some((value) => !Number.isFinite(value) || value <= 0)) {
      return false;
    }
    const mean = tracks.reduce((sum, value) => sum + value, 0) / tracks.length;
    return item.width >= rootRect.width * 0.75 &&
      Math.max(...tracks.map((value) => Math.abs(value - mean))) <= mean * 0.12;
  });
  cssGridCandidates.sort((left, right) => right.width - left.width || right.height - left.height);
  let selected = null;
  let strategy = null;
  if (cssGridCandidates.length) {
    const item = cssGridCandidates[0];
    selected = {left: item.left, right: item.right, width: item.width};
    strategy = 'css-grid-seven-tracks';
  }
  const candidates = described.filter((rect) => rect.width >= rootRect.width * 0.08 &&
    rect.width <= rootRect.width * 0.20 && rect.height >= 10);
  const unique = [];
  for (const candidate of candidates) {
    if (!unique.some((item) => Math.abs(item.left - candidate.left) <= 2 &&
      Math.abs(item.right - candidate.right) <= 2)) unique.push(candidate);
  }
  unique.sort((left, right) => left.left - right.left || right.height - left.height);
  let best = null;
  for (const first of unique) {
    const sequence = [first];
    while (sequence.length < 7) {
      const previous = sequence[sequence.length - 1];
      const next = unique.find((item) => item.left > previous.left + 2 &&
        Math.abs(item.left - previous.right) <= first.width * 0.18 &&
        Math.abs(item.width - first.width) <= first.width * 0.12);
      if (!next) break;
      sequence.push(next);
    }
    if (sequence.length !== 7) continue;
    const left = sequence[0].left;
    const right = sequence[6].right;
    const span = right - left;
    if (span < rootRect.width * 0.75 || left < rootRect.left - 3 ||
      right > rootRect.right + 3) continue;
    const semantic = sequence.filter((item) =>
      ['columnheader', 'gridcell', 'column'].includes(item.role)).length;
    const score = span + semantic * 1000 +
      sequence.reduce((sum, item) => sum + Math.min(item.height, 500), 0) / 1000;
    if (!best || score > best.score) best = {left, right, score, sequence};
  }
  if (!selected && best) {
    selected = {left: best.left, right: best.right, width: best.right - best.left};
    strategy = 'seven-equal-structural-columns';
  }
  return {
    selected,
    strategy,
    root: describe(root),
    css_grid_candidates: cssGridCandidates.slice(0, 30),
    day_column_candidates: unique.slice(0, 120),
    selected_day_columns: best ? best.sequence : [],
  };
}
"""
CAPTURE_PAGE_DIAGNOSTIC_SCRIPT = """
() => {
  const visible = (element) => {
    const rect = element.getBoundingClientRect();
    const style = window.getComputedStyle(element);
    return rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden' &&
      style.display !== 'none';
  };
  const headings = Array.from(document.querySelectorAll(
    "[role='heading'], h1, h2, h3, header button, [aria-label]"))
    .filter(visible).map((element) => ({
      text: (element.textContent || '').trim(),
      aria_label: element.getAttribute('aria-label'),
      role: element.getAttribute('role'),
      tag: element.tagName.toLowerCase(),
    })).filter((item) => (item.text || item.aria_label) &&
      (item.text.length <= 100) && ((item.aria_label || '').length <= 160)).slice(0, 100);
  return {
    url: window.location.href,
    document_title: document.title,
    viewport: {width: window.innerWidth, height: window.innerHeight,
      device_pixel_ratio: window.devicePixelRatio},
    headings,
  };
}
"""
CALENDAR_NAVIGATION_DIAGNOSTIC_SCRIPT = r"""
(expectedAccount) => {
  const visible = (element) => {
    const rect = element.getBoundingClientRect();
    const style = window.getComputedStyle(element);
    return rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden' &&
      style.display !== 'none';
  };
  const visibleNodes = (selector) => Array.from(document.querySelectorAll(selector))
    .filter(visible);
  const loginControls = visibleNodes(
    "input[type='email'], input[type='password'], [autocomplete='username'], " +
    "[autocomplete='current-password']");
  const shellCandidates = visibleNodes("[role='main'], main, [role='grid']");
  const timeLabels = visibleNodes("[aria-label]").filter((element) => {
    const value = element.getAttribute('aria-label') || '';
    return /(^|\s)([0-1]?\d|2[0-3])(:00)?(\s|$)|\b(AM|PM)\b/i.test(value);
  });
  const accountControls = visibleNodes(
    "[aria-label*='Google Account'], [aria-label*='Conta do Google'], " +
    "a[href*='SignOutOptions'], a[href*='AccountChooser'], img[alt*='@']");
  const accountEvidence = accountControls.map((element) => [
    element.getAttribute('aria-label') || '',
    element.getAttribute('alt') || '',
    element.getAttribute('title') || '',
    element.textContent || '',
  ].join(' '));
  const detectedAccounts = [...new Set(accountEvidence.flatMap((value) =>
    value.match(/[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}/ig) || []))];
  const dateMarkers = visibleNodes(
    "[data-datekey], [data-date], [datetime], [aria-label]"
  ).slice(0, 500).map((element) => ({
    data_datekey: element.getAttribute('data-datekey'),
    data_date: element.getAttribute('data-date'),
    datetime: element.getAttribute('datetime'),
    aria_label: element.getAttribute('aria-label'),
    text: (element.textContent || '').trim().slice(0, 120),
  })).filter((item) => item.data_datekey || item.data_date || item.datetime || item.aria_label);
  const host = window.location.hostname;
  const path = window.location.pathname;
  return {
    url: window.location.href,
    document_title: document.title,
    host,
    path,
    login_page_detected: host === 'accounts.google.com' || loginControls.length > 0,
    calendar_shell_detected: host === 'calendar.google.com' && shellCandidates.length > 0,
    week_view_detected: /\/week(?:\/|$)/.test(path) ||
      (shellCandidates.length > 0 && timeLabels.length >= 4),
    detected_accounts: detectedAccounts,
    expected_account: expectedAccount || null,
    date_markers: dateMarkers,
  };
}
"""


class ManualLoginRequired(CalendarAnimError):
    """A persistent browser profile has no usable authenticated Calendar session."""

    non_retryable = True


class ProfileAccountMismatch(CalendarAnimError):
    """A persistent browser profile is authenticated as a different Google account."""

    non_retryable = True


def _dates_from_navigation_markers(raw: object) -> list[date]:
    if not isinstance(raw, list):
        return []
    found: set[date] = set()
    pattern = re.compile(r"(?<!\d)(20\d{2})[-/]?(\d{2})[-/]?(\d{2})(?!\d)")
    for item in raw:
        if not isinstance(item, dict):
            continue
        for value in item.values():
            if not isinstance(value, str):
                continue
            for match in pattern.finditer(value):
                try:
                    found.add(date(*(int(part) for part in match.groups())))
                except ValueError:
                    continue
    return sorted(found)


def classify_calendar_navigation(
    raw: object,
    expected_week: date,
    *,
    profile_name: str | None = None,
    browser_profile_path: Path | None = None,
    expected_account: str | None = None,
) -> dict[str, object]:
    """Classify Calendar navigation from rendered UI first, with URL as fallback."""

    source = raw if isinstance(raw, dict) else {}
    url = str(source.get("url", ""))
    title = str(source.get("document_title", ""))
    login_page = bool(source.get("login_page_detected", False))
    shell = bool(source.get("calendar_shell_detected", False))
    week_view = bool(source.get("week_view_detected", False))
    detected_accounts = (
        [str(item) for item in source.get("detected_accounts", []) if isinstance(item, str)]
        if isinstance(source.get("detected_accounts", []), list)
        else []
    )
    normalized_expected = expected_account.casefold() if expected_account else None
    normalized_detected = {item.casefold() for item in detected_accounts}
    account_mismatch = bool(
        normalized_expected
        and normalized_detected
        and normalized_expected not in normalized_detected
    )
    visible_dates = _dates_from_navigation_markers(source.get("date_markers"))
    expected_dates = {expected_week + timedelta(days=offset) for offset in range(7)}
    visible_expected_dates = sorted(expected_dates.intersection(visible_dates))
    ui_week_match = expected_week in visible_expected_dates or len(visible_expected_dates) >= 4
    match = WEEK_PATH_PATTERN.search(url)
    url_week = date(*(int(part) for part in match.groups())) if match is not None else None
    url_week_match = url_week == expected_week
    week_matches = week_view and (ui_week_match or (not visible_dates and url_week_match))
    if login_page:
        state = "manual_login_required"
    elif account_mismatch:
        state = "profile_account_mismatch"
    elif week_matches:
        state = "ready"
    elif shell and week_view:
        state = "wrong_week"
    elif shell:
        state = "wrong_calendar_view"
    elif "calendar.google.com" in url:
        state = "calendar_shell_missing"
    else:
        state = "redirect_or_interstitial"
    source_kind = "visible_ui" if ui_week_match else ("url_fallback" if url_week_match else "none")
    return {
        "profile": profile_name,
        "browser_profile_path": str(browser_profile_path) if browser_profile_path else None,
        "current_url": url,
        "page_title": title,
        "expected_week": expected_week.isoformat(),
        "visible_week_date": (
            visible_expected_dates[0].isoformat()
            if visible_expected_dates
            else (visible_dates[0].isoformat() if visible_dates else None)
        ),
        "visible_dates": [value.isoformat() for value in visible_dates],
        "url_week": url_week.isoformat() if url_week else None,
        "week_detection_source": source_kind,
        "logged_in_detection": shell and not login_page,
        "login_page_detection": login_page,
        "calendar_shell_detection": shell,
        "week_view_detection": week_view,
        "week_matches": week_matches,
        "expected_google_account": expected_account,
        "detected_google_accounts": detected_accounts,
        "account_match": (
            None if not expected_account or not detected_accounts else not account_mismatch
        ),
        "state": state,
    }


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


@dataclass(frozen=True)
class EventPopulationAudit:
    raw_node_count: int
    leaf_node_count: int
    unique_event_count: int
    identity_digest: str
    wrapper_nodes_removed: int
    structural_nodes_removed: int
    duplicate_leaf_nodes_removed: int
    rendered_color_counts: dict[str, int]


@dataclass(frozen=True)
class AnimationReadiness:
    samples: list[dict[str, object]]
    raw_node_count: int
    unique_event_count: int
    stabilization_seconds: float
    coordinate_scale: float
    grid_bounds: dict[str, float]
    grid_diagnostics: dict[str, object] | None = None


class CaptureReadinessError(CalendarAnimError):
    def __init__(self, message: str, samples: list[dict[str, object]]) -> None:
        super().__init__(message)
        self.samples = samples


def deduplicate_event_records(records: list[dict[str, object]]) -> EventPopulationAudit:
    """Collapse Calendar wrapper/chip layers into unique visible event chips."""

    leaves = [record for record in records if not bool(record["contains_matching_descendant"])]
    usable: list[dict[str, object]] = []
    structural = 0
    for record in leaves:
        width = _record_float(record, "width")
        height = _record_float(record, "height")
        viewport_width = _record_float(record, "viewport_width")
        if (
            not bool(record["in_viewport"])
            or width <= 0
            or height <= 0
            or width >= viewport_width * 0.25
        ):
            structural += 1
            continue
        usable.append(record)
    unique: dict[str, dict[str, object]] = {}
    for record in usable:
        explicit = next(
            (
                str(record[key])
                for key in ("data_eventid", "data_eventchip", "href")
                if record.get(key)
            ),
            None,
        )
        geometry = ":".join(
            f"{_record_float(record, key):.2f}" for key in ("x", "y", "width", "height")
        )
        identity = (
            f"id:{explicit}:geometry:{geometry}"
            if explicit
            else f"geometry:{geometry}:{record.get('aria_label', '')}"
        )
        unique.setdefault(identity, record)
    digest = hashlib.sha256("\n".join(sorted(unique)).encode("utf-8")).hexdigest()
    colors: dict[str, int] = {}
    for record in unique.values():
        value = record.get("visible_color")
        if value:
            key = str(value)
            colors[key] = colors.get(key, 0) + 1
    return EventPopulationAudit(
        raw_node_count=len(records),
        leaf_node_count=len(leaves),
        unique_event_count=len(unique),
        identity_digest=digest,
        wrapper_nodes_removed=len(records) - len(leaves),
        structural_nodes_removed=structural,
        duplicate_leaf_nodes_removed=len(usable) - len(unique),
        rendered_color_counts=colors,
    )


def _record_float(record: dict[str, object], key: str) -> float:
    value = record.get(key)
    if not isinstance(value, (int, float)):
        raise CalendarAnimError(f"Calendar DOM record has invalid {key}")
    return float(value)


def wait_for_stable_population(
    sample: Callable[[], tuple[EventPopulationAudit, float, dict[str, float]]],
    *,
    expected_count: int,
    timeout_seconds: float,
    interval_seconds: float,
    stable_samples: int,
    expected_coordinate_scale: float | None = None,
    clock: Callable[[], float] = time.monotonic,
    sleeper: Callable[[float], None] = time.sleep,
) -> AnimationReadiness:
    started = clock()
    history: list[dict[str, object]] = []
    stable = 0
    previous: tuple[int, str, float, float] | None = None
    last: tuple[EventPopulationAudit, float, dict[str, float]] | None = None
    while clock() - started <= timeout_seconds:
        audit, coordinate_scale, bounds = sample()
        last = (audit, coordinate_scale, bounds)
        elapsed = clock() - started
        signature = (
            0,
            "diagnostic-only",
            round(coordinate_scale, 4),
            round(bounds["width"], 2),
        )
        layout_valid = (
            expected_coordinate_scale is None
            or abs(coordinate_scale - expected_coordinate_scale) <= 0.02
        )
        population_warning = audit.unique_event_count < max(1, round(expected_count * 0.75))
        loaded = layout_valid
        stable = stable + 1 if loaded and signature == previous else (1 if loaded else 0)
        history.append(
            {
                "elapsed_seconds": elapsed,
                "raw_dom_nodes": audit.raw_node_count,
                "leaf_dom_nodes": audit.leaf_node_count,
                "unique_event_chips": audit.unique_event_count,
                "wrapper_nodes_removed": audit.wrapper_nodes_removed,
                "structural_nodes_removed": audit.structural_nodes_removed,
                "duplicate_leaf_nodes_removed": audit.duplicate_leaf_nodes_removed,
                "identity_digest": audit.identity_digest,
                "coordinate_scale": coordinate_scale,
                "grid_width": bounds["width"],
                "layout_coordinate_valid": layout_valid,
                "event_population_warning": population_warning,
                "stable_sequence": stable,
            }
        )
        if stable >= stable_samples:
            return AnimationReadiness(
                samples=history,
                raw_node_count=audit.raw_node_count,
                unique_event_count=audit.unique_event_count,
                stabilization_seconds=elapsed,
                coordinate_scale=coordinate_scale,
                grid_bounds=bounds,
            )
        previous = signature
        sleeper(interval_seconds)
    unique = last[0].unique_event_count if last is not None else 0
    raise CaptureReadinessError(
        f"CAPTURE LOAD FAILURE: event population did not stabilize before "
        f"{timeout_seconds:.0f}s (last unique count {unique}, expected reference {expected_count})",
        history,
    )


def wait_for_stable_visual_grid(
    sample: Callable[
        [],
        tuple[
            bytes,
            EventPopulationAudit,
            float,
            dict[str, float],
            dict[str, object],
        ],
    ],
    *,
    expected_count: int,
    timeout_seconds: float,
    interval_seconds: float,
    stable_samples: int,
    expected_coordinate_scale: float | None = None,
    clock: Callable[[], float] = time.monotonic,
    sleeper: Callable[[float], None] = time.sleep,
) -> AnimationReadiness:
    """Wait only for stable grid pixels/geometry; event population is diagnostic."""

    started = clock()
    history: list[dict[str, object]] = []
    stable = 0
    previous: tuple[str, float, float, float] | None = None
    while clock() - started <= timeout_seconds:
        snapshot, audit, coordinate_scale, bounds, diagnostics = sample()
        elapsed = clock() - started
        digest = hashlib.sha256(snapshot).hexdigest()
        signature = (
            digest,
            round(bounds["left"], 2),
            round(bounds["width"], 2),
            round(coordinate_scale, 4),
        )
        layout_valid = (
            expected_coordinate_scale is None
            or abs(coordinate_scale - expected_coordinate_scale) <= 0.02
        )
        stable = (
            stable + 1 if layout_valid and signature == previous else (1 if layout_valid else 0)
        )
        history.append(
            {
                "elapsed_seconds": elapsed,
                "grid_snapshot_sha256": digest,
                "grid_left": bounds["left"],
                "grid_width": bounds["width"],
                "coordinate_scale": coordinate_scale,
                "layout_coordinate_valid": layout_valid,
                "raw_dom_nodes": audit.raw_node_count,
                "unique_event_chips": audit.unique_event_count,
                "expected_event_reference": expected_count,
                "event_population_warning": audit.unique_event_count
                < max(1, round(expected_count * 0.75)),
                "stable_sequence": stable,
                "grid_strategy": diagnostics.get("strategy"),
            }
        )
        if stable >= stable_samples:
            return AnimationReadiness(
                samples=history,
                raw_node_count=audit.raw_node_count,
                unique_event_count=audit.unique_event_count,
                stabilization_seconds=elapsed,
                coordinate_scale=coordinate_scale,
                grid_bounds=bounds,
                grid_diagnostics=diagnostics,
            )
        previous = signature
        sleeper(interval_seconds)
    raise CaptureReadinessError(
        f"CAPTURE LOAD FAILURE: visual week grid did not stabilize before {timeout_seconds:.0f}s",
        history,
    )


def logical_grid_clip(
    structural_bounds: dict[str, float],
    time_bounds: dict[str, float],
    scale_x: float,
    scale_y: float,
) -> dict[str, float]:
    """Scale content-independent week-grid bounds into screenshot coordinates."""

    clip = {
        "x": structural_bounds["left"] * scale_x,
        "y": time_bounds["y"] * scale_y,
        "width": structural_bounds["width"] * scale_x,
        "height": time_bounds["height"] * scale_y,
    }
    if clip["width"] <= 0 or clip["height"] <= 0:
        raise CalendarAnimError("Detected Calendar logical grid has invalid geometry")
    return clip


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


def structural_grid_bounds_from_diagnostics(raw: object) -> dict[str, float]:
    """Validate a content-independent grid selection returned by the browser."""

    if not isinstance(raw, dict):
        raise CalendarAnimError("CAPTURE LOAD FAILURE: Calendar week-grid diagnostics are invalid")
    selected = raw.get("selected")
    if not isinstance(selected, dict):
        raise CalendarAnimError(
            "CAPTURE LOAD FAILURE: no content-independent Calendar week grid was found"
        )
    try:
        bounds = {
            "left": float(selected["left"]),
            "right": float(selected["right"]),
            "width": float(selected["width"]),
        }
    except (KeyError, TypeError, ValueError) as error:
        raise CalendarAnimError(
            "CAPTURE LOAD FAILURE: Calendar structural grid geometry is invalid"
        ) from error
    if bounds["width"] <= 0 or bounds["right"] <= bounds["left"]:
        raise CalendarAnimError("CAPTURE LOAD FAILURE: Calendar structural grid has invalid bounds")
    return bounds


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
        self._logical_grid_clip: dict[str, float] | None = None
        self._week_header_grid_clip: dict[str, float] | None = None
        self._last_animation_readiness: AnimationReadiness | None = None
        self._last_grid_diagnostics: dict[str, object] | None = None
        self._last_visible_window_diagnostics: dict[str, object] | None = None
        self._last_navigation_diagnostics: dict[str, object] | None = None
        self._target_week: date | None = None
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
        self._logical_grid_clip = None
        self._week_header_grid_clip = None
        self._last_animation_readiness = None
        self._last_grid_diagnostics = None
        self._last_visible_window_diagnostics = None
        self._last_navigation_diagnostics = None
        self._target_week = None
        self._applied_zoom_percent = None

    def open_week(self, week_start: date) -> None:
        page = self._require_page()
        self._target_week = week_start
        page.goto(calendar_week_url(week_start), wait_until="domcontentloaded")
        if self.config.browser_zoom_percent == 100:
            page.keyboard.press("Control+0")
            self._applied_zoom_percent = 100.0
        else:
            page.bring_to_front()
            self._applied_zoom_percent = self._verify_browser_zoom(self.config.browser_zoom_percent)
        self._capture_clip = None
        self._header_clip = None
        self._time_window_clip = None
        self._logical_grid_clip = None
        self._week_header_grid_clip = None
        self._last_animation_readiness = None
        self._last_grid_diagnostics = None
        self._last_visible_window_diagnostics = None
        self._last_navigation_diagnostics = None

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
        self._ensure_target_week(week_start)
        region = self._find_visible_capture_region()
        region.wait_for(state="visible")
        if minimum_event_count > 0:
            page.locator(EVENT_SELECTORS).first.wait_for(state="visible")
        header_clip, time_clip = self._position_visible_window(region)
        self._wait_for_stable_snapshots(time_clip)
        navigation = self.inspect_navigation(week_start)
        self._raise_for_navigation_state(navigation)
        self._header_clip = header_clip
        self._time_window_clip = time_clip
        self._capture_clip = time_clip

    def capture(self, output_path: Path) -> None:
        if self._capture_clip is None:
            raise CalendarAnimError("Calendar region is not ready for capture")
        if self._header_clip is None or self._time_window_clip is None:
            raise CalendarAnimError("Calendar header and time window are not ready for capture")
        page = self._require_page()
        header_bytes = page.screenshot(animations="disabled", scale="css", clip=self._header_clip)
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

    def capture_viewport(self, output_path: Path) -> None:
        """Save the unmodified visible browser viewport for capture diagnostics."""

        page = self._require_page()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=output_path, animations="disabled", scale="css")

    def capture_logical_event_grid(self, output_path: Path) -> dict[str, object]:
        """Capture the stable seven-day structural grid and return DOM diagnostics."""

        if (
            self._logical_grid_clip is None
            or self._applied_zoom_percent is None
            or self._last_animation_readiness is None
        ):
            raise CalendarAnimError("Calendar logical grid is not ready for capture")
        page = self._require_page()
        clip = self._logical_grid_clip
        output_path.parent.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=output_path, animations="disabled", scale="css", clip=clip)
        readiness = self._last_animation_readiness
        final_audit = self.animation_event_audit()
        return {
            "event_count": final_audit.unique_event_count,
            "raw_dom_nodes": final_audit.raw_node_count,
            "leaf_dom_nodes": final_audit.leaf_node_count,
            "unique_event_chips": final_audit.unique_event_count,
            "wrapper_nodes_removed": final_audit.wrapper_nodes_removed,
            "structural_nodes_removed": final_audit.structural_nodes_removed,
            "duplicate_leaf_nodes_removed": final_audit.duplicate_leaf_nodes_removed,
            "rendered_color_counts": final_audit.rendered_color_counts,
            "dom_population_samples": readiness.samples,
            "stabilization_seconds": readiness.stabilization_seconds,
            "coordinate_scale": readiness.coordinate_scale,
            "structural_grid_bounds": readiness.grid_bounds,
            "grid_diagnostics": readiness.grid_diagnostics,
            "grid_left": clip["x"],
            "grid_top": clip["y"],
            "grid_right": clip["x"] + clip["width"],
            "grid_bottom": clip["y"] + clip["height"],
            "navigation_complete": True,
            "applied_zoom_percent": self._applied_zoom_percent,
            "logical_clip": clip,
            "logical_cell_width": clip["width"] / 126,
            "logical_cell_height": clip["height"] / 72,
        }

    def capture_header_event_grid(self, output_path: Path) -> dict[str, object]:
        """Capture the structural week header plus the exact 06:00-00:00 grid."""

        if self._week_header_grid_clip is None or self._logical_grid_clip is None:
            raise CalendarAnimError("Calendar header grid is not ready for capture")
        header_clip = self._week_header_grid_clip
        event_grid_clip = self._logical_grid_clip
        output_path.parent.mkdir(parents=True, exist_ok=True)
        page = self._require_page()
        header_bytes = page.screenshot(animations="disabled", scale="css", clip=header_clip)
        event_grid_bytes = page.screenshot(animations="disabled", scale="css", clip=event_grid_clip)
        header: Image.Image | None = None
        event_grid: Image.Image | None = None
        composed: Image.Image | None = None
        try:
            with Image.open(BytesIO(header_bytes)) as opened:
                header = opened.convert("RGB")
            with Image.open(BytesIO(event_grid_bytes)) as opened:
                event_grid = opened.convert("RGB")
            if header.width != event_grid.width:
                raise CalendarAnimError("Calendar header and event-grid structural widths differ")
            composed = Image.new("RGB", (header.width, header.height + event_grid.height))
            composed.paste(header, (0, 0))
            composed.paste(event_grid, (0, header.height))
            composed.save(output_path)
            dimensions = [composed.width, composed.height]
        finally:
            for image in (header, event_grid, composed):
                if image is not None:
                    image.close()
        return {
            "header_grid_bounds": {
                "header_clip": header_clip,
                "event_grid_clip": event_grid_clip,
                "composite_dimensions": dimensions,
                "native_header_height": dimensions[1] - event_grid.height,
                "native_grid_height": event_grid.height,
            },
            "header_included": True,
            "vertical_interval": "06:00-00:00",
            "horizontal_bounds_source": "structural-week-grid",
            "empty_pre_06_interval_removed": True,
        }

    def animation_event_audit(self) -> EventPopulationAudit:
        """Return unique visible animation chips with wrapper diagnostics."""

        raw = (
            self._require_page().locator(EVENT_SELECTORS).evaluate_all(ANIMATION_EVENT_AUDIT_SCRIPT)
        )
        if not isinstance(raw, list) or not all(isinstance(item, dict) for item in raw):
            raise CalendarAnimError("Calendar animation DOM audit returned invalid data")
        return deduplicate_event_records(raw)

    def wait_for_animation_events(self, expected_count: int) -> AnimationReadiness:
        """Wait for stable grid pixels; event counts remain diagnostic only."""

        readiness = wait_for_stable_visual_grid(
            self._animation_visual_sample,
            expected_count=expected_count,
            timeout_seconds=self.config.ready_timeout_seconds,
            interval_seconds=max(1.0, min(self.config.stabilization_seconds, 2.0)),
            stable_samples=3,
            expected_coordinate_scale=native_browser_zoom_factor(self.config.browser_zoom_percent),
        )
        self._refresh_animation_capture_geometry(readiness)
        self._last_animation_readiness = readiness
        return readiness

    def _animation_visual_sample(
        self,
    ) -> tuple[
        bytes,
        EventPopulationAudit,
        float,
        dict[str, float],
        dict[str, object],
    ]:
        region = self._find_visible_capture_region()
        bounds = self._structural_grid_bounds(region)
        scale_x, scale_y = self._coordinate_scale()
        if abs(scale_x - scale_y) > 0.02:
            raise CalendarAnimError("CAPTURE LOAD FAILURE: Calendar x/y coordinate scales disagree")
        raw = region.evaluate(
            POSITION_VISIBLE_WINDOW_SCRIPT,
            {
                "startHour": self.config.visible_start_hour,
                "endHour": self.config.visible_end_hour,
            },
        )
        if isinstance(raw, dict):
            self._last_visible_window_diagnostics = raw
        metrics = VisibleWindowMetrics.from_browser(raw)
        time_bounds = time_window_clip(
            metrics, self.config.visible_start_hour, self.config.visible_end_hour
        )
        clip = logical_grid_clip(bounds, time_bounds, scale_x, scale_y)
        snapshot = self._require_page().screenshot(animations="disabled", scale="css", clip=clip)
        diagnostics = self._last_grid_diagnostics or {}
        return snapshot, self.animation_event_audit(), scale_x, bounds, diagnostics

    def _animation_population_sample(
        self,
    ) -> tuple[EventPopulationAudit, float, dict[str, float]]:
        region = self._find_visible_capture_region()
        bounds = self._structural_grid_bounds(region)
        scale_x, scale_y = self._coordinate_scale()
        if abs(scale_x - scale_y) > 0.02:
            raise CalendarAnimError("CAPTURE LOAD FAILURE: Calendar x/y coordinate scales disagree")
        return self.animation_event_audit(), scale_x, bounds

    def _refresh_animation_capture_geometry(self, readiness: AnimationReadiness) -> None:
        region = self._find_visible_capture_region()
        raw = region.evaluate(
            POSITION_VISIBLE_WINDOW_SCRIPT,
            {
                "startHour": self.config.visible_start_hour,
                "endHour": self.config.visible_end_hour,
            },
        )
        if isinstance(raw, dict):
            self._last_visible_window_diagnostics = raw
        metrics = VisibleWindowMetrics.from_browser(raw)
        time_bounds = time_window_clip(
            metrics, self.config.visible_start_hour, self.config.visible_end_hour
        )
        header_bounds = week_header_clip(metrics)
        scale_x, scale_y = self._coordinate_scale()
        bounds = self._structural_grid_bounds(region)
        self._logical_grid_clip = logical_grid_clip(bounds, time_bounds, scale_x, scale_y)
        self._week_header_grid_clip = {
            "x": bounds["left"] * scale_x,
            "y": header_bounds["y"] * scale_y,
            "width": bounds["width"] * scale_x,
            "height": header_bounds["height"] * scale_y,
        }
        self._time_window_clip = {
            "x": time_bounds["x"] * scale_x,
            "y": time_bounds["y"] * scale_y,
            "width": time_bounds["width"] * scale_x,
            "height": time_bounds["height"] * scale_y,
        }
        readiness.grid_bounds.update(bounds)

    def _structural_grid_bounds(self, region: Any) -> dict[str, float]:
        raw = region.evaluate(STRUCTURAL_WEEK_GRID_SCRIPT)
        self._last_grid_diagnostics = raw if isinstance(raw, dict) else {"invalid_result": raw}
        return structural_grid_bounds_from_diagnostics(raw)

    def capture_debug_state(self) -> dict[str, object]:
        """Return read-only page/grid/event diagnostics for a capture attempt."""

        page = self._require_page()
        raw = page.evaluate(CAPTURE_PAGE_DIAGNOSTIC_SCRIPT)
        page_state = raw if isinstance(raw, dict) else {"invalid_page_state": raw}
        try:
            audit = self.animation_event_audit()
            event_state: dict[str, object] = {
                "raw_dom_nodes": audit.raw_node_count,
                "unique_event_chips": audit.unique_event_count,
                "wrapper_nodes_removed": audit.wrapper_nodes_removed,
                "structural_nodes_removed": audit.structural_nodes_removed,
                "duplicate_leaf_nodes_removed": audit.duplicate_leaf_nodes_removed,
            }
        except CalendarAnimError as error:
            event_state = {"event_audit_error": str(error)}
        navigation = self._last_navigation_diagnostics
        if self._target_week is not None:
            try:
                navigation = self.inspect_navigation(self._target_week)
            except CalendarAnimError as error:
                navigation = {"navigation_diagnostic_error": str(error)}
        return {
            **page_state,
            "requested_zoom_percent": self.config.browser_zoom_percent,
            "applied_zoom_percent": self._applied_zoom_percent,
            "time_window_clip": self._time_window_clip,
            "logical_grid_clip": self._logical_grid_clip,
            "week_header_grid_clip": self._week_header_grid_clip,
            "scroll_position": self._last_visible_window_diagnostics,
            "grid_diagnostics": self._last_grid_diagnostics,
            "navigation": navigation,
            "events": event_state,
        }

    def inspect_navigation(self, expected_week: date) -> dict[str, object]:
        """Inspect rendered Calendar/session state without mutating Calendar data."""

        raw = self._require_page().evaluate(
            CALENDAR_NAVIGATION_DIAGNOSTIC_SCRIPT,
            self.config.expected_google_account,
        )
        result = classify_calendar_navigation(
            raw,
            expected_week,
            profile_name=self.config.profile_name,
            browser_profile_path=self.config.profile_directory.resolve(),
            expected_account=self.config.expected_google_account,
        )
        result["zoom_expected"] = self.config.browser_zoom_percent
        result["zoom_applied"] = self._applied_zoom_percent
        result["expected_calendar_name"] = self.config.expected_calendar_name
        self._last_navigation_diagnostics = result
        return result

    def _ensure_target_week(self, expected_week: date) -> dict[str, object]:
        navigation = self.inspect_navigation(expected_week)
        self._raise_for_session_state(navigation)
        if navigation["state"] == "ready":
            return navigation

        page = self._require_page()
        page.goto(CALENDAR_ROOT_URL, wait_until="domcontentloaded")
        page.wait_for_load_state("domcontentloaded")
        root_state = self.inspect_navigation(expected_week)
        self._raise_for_session_state(root_state)

        page.goto(calendar_week_url(expected_week), wait_until="domcontentloaded")
        page.wait_for_load_state("domcontentloaded")
        navigation = self.inspect_navigation(expected_week)
        self._raise_for_navigation_state(navigation)
        return navigation

    def _raise_for_session_state(self, navigation: dict[str, object]) -> None:
        profile = self.config.profile_name or "unknown"
        state = navigation.get("state")
        if state == "manual_login_required":
            raise ManualLoginRequired(f"MANUAL LOGIN REQUIRED\nPROFILE: {profile}")
        if state == "profile_account_mismatch":
            detected = navigation.get("detected_google_accounts")
            raise ProfileAccountMismatch(
                f"PROFILE ACCOUNT MISMATCH\nPROFILE: {profile}\n"
                f"EXPECTED: {self.config.expected_google_account}\nDETECTED: {detected}"
            )

    def _raise_for_navigation_state(self, navigation: dict[str, object]) -> None:
        self._raise_for_session_state(navigation)
        if navigation.get("state") != "ready":
            raise CalendarAnimError(
                "Calendar navigation did not reach the target visible week; "
                f"state={navigation.get('state')}, url={navigation.get('current_url')}, "
                f"visible_week={navigation.get('visible_week_date')}, "
                f"expected_week={navigation.get('expected_week')}"
            )

    def _coordinate_scale(self) -> tuple[float, float]:
        raw = self._require_page().evaluate(
            "({width: window.innerWidth, height: window.innerHeight})"
        )
        if not isinstance(raw, dict):
            raise CalendarAnimError("CAPTURE LOAD FAILURE: viewport geometry is unavailable")
        width = float(raw.get("width", 0))
        height = float(raw.get("height", 0))
        if width <= 0 or height <= 0:
            raise CalendarAnimError("CAPTURE LOAD FAILURE: viewport geometry is invalid")
        return self.config.viewport_width / width, self.config.viewport_height / height

    def reload_current_week(self, week_start: date, minimum_event_count: int) -> None:
        """Reload the current week and re-establish the exact capture window."""

        page = self._require_page()
        page.reload(wait_until="domcontentloaded")
        self.wait_until_ready(week_start, minimum_event_count)

    def collect_zero_width_event_geometry(
        self,
        summaries: list[str],
        color_ids: list[str],
    ) -> list[dict[str, object]]:
        """Measure the 18 calibrated invisible-summary event chips in the live DOM."""

        if len(summaries) != len(color_ids):
            raise CalendarAnimError("summary/colorId ordering metadata differs in length")
        page = self._require_page()
        raw = page.locator(EVENT_SELECTORS).evaluate_all(
            """
            (elements, expected) => {
              const transparent = new Set(['', 'transparent', 'rgba(0, 0, 0, 0)']);
              const opaque = (value) => !transparent.has(value || '');
              const auditStyle = (node) => {
                const style = window.getComputedStyle(node);
                const before = window.getComputedStyle(node, '::before');
                const after = window.getComputedStyle(node, '::after');
                const customProperties = {};
                for (let index = 0; index < style.length; index += 1) {
                  const name = style[index];
                  if (name.startsWith('--')) customProperties[name] = style.getPropertyValue(name);
                }
                return {
                  node,
                  rect: node.getBoundingClientRect(),
                  background: style.backgroundColor,
                  borders: [style.borderTopColor, style.borderRightColor,
                    style.borderBottomColor, style.borderLeftColor],
                  customProperties,
                  inlineStyle: node.getAttribute('style') || '',
                  pseudoBackgrounds: [before.backgroundColor, after.backgroundColor]
                    .filter(opaque),
                };
              };
              const result = [];
              for (const element of elements) {
                const content = element.textContent || '';
                const aria = element.getAttribute('aria-label') || '';
                for (let slot = 0; slot < expected.summaries.length; slot += 1) {
                  const summary = expected.summaries[slot];
                  if (!content.includes(summary) && !aria.includes(summary)) continue;
                  const rect = element.getBoundingClientRect();
                  if (rect.width <= 0 || rect.height <= 0) continue;
                  const audits = [element, ...element.querySelectorAll('*')]
                    .map(auditStyle)
                    .filter((item) => item.rect.width > 0 && item.rect.height > 0);
                  const elementAudit = audits[0];
                  const visibleCandidates = audits.flatMap((item, index) => {
                    const candidates = [];
                    if (opaque(item.background)) {
                      candidates.push({
                        color: item.background,
                        source: index === 0 ? 'element-background' : 'descendant-background',
                        area: item.rect.width * item.rect.height,
                      });
                    }
                    for (const color of item.pseudoBackgrounds) {
                      candidates.push({color, source: 'pseudo-element-background',
                        area: item.rect.width * item.rect.height});
                    }
                    return candidates;
                  });
                  visibleCandidates.sort((left, right) => right.area - left.area);
                  const rendered = visibleCandidates[0] || null;
                  result.push({
                    slot_index: slot,
                    summary,
                    summary_codepoints: [...summary]
                      .map((value) => {
                        const code = value.codePointAt(0).toString(16).toUpperCase();
                        return `U+${code.padStart(4, '0')}`;
                      })
                      .join(' '),
                    color_id_expected: expected.colorIds[slot],
                    x: rect.x,
                    width: rect.width,
                    y: rect.y,
                    height: rect.height,
                    css_background_color: elementAudit.background,
                    rendered_color: rendered ? rendered.color : null,
                    rendered_color_source: rendered ? rendered.source : null,
                    element_border_colors: [...new Set(elementAudit.borders)],
                    descendant_background_colors: [...new Set(audits.slice(1)
                      .map((item) => item.background).filter(opaque))],
                    css_custom_properties: elementAudit.customProperties,
                    inline_style: elementAudit.inlineStyle,
                    pseudo_background_colors: [...new Set(audits.flatMap(
                      (item) => item.pseudoBackgrounds))],
                  });
                  break;
                }
              }
              return result;
            }
            """,
            {"summaries": summaries, "colorIds": color_ids},
        )
        if not isinstance(raw, list):
            raise CalendarAnimError("Calendar DOM ordering measurement returned invalid data")
        return raw

    def _find_visible_capture_region(self) -> Any:
        page = self._require_page()
        for selector in CALENDAR_REGION_SELECTORS:
            candidates = page.locator(selector)
            for index in range(candidates.count()):
                candidate = candidates.nth(index)
                if candidate.is_visible():
                    return candidate
        raise CalendarAnimError("Could not find a visible Google Calendar week grid")

    def _position_visible_window(self, region: Any) -> tuple[dict[str, float], dict[str, float]]:
        raw = region.evaluate(
            POSITION_VISIBLE_WINDOW_SCRIPT,
            {
                "startHour": self.config.visible_start_hour,
                "endHour": self.config.visible_end_hour,
            },
        )
        if isinstance(raw, dict):
            self._last_visible_window_diagnostics = raw
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
