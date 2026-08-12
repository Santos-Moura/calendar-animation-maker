import hashlib
import json
import os
import re
from collections import Counter
from datetime import UTC, date, datetime, time, timedelta
from enum import StrEnum
from pathlib import Path
from zoneinfo import ZoneInfo

from PIL import Image, ImageDraw
from pydantic import BaseModel, Field, ValidationError, model_validator

from calendar_anim.calendar.multi_frame.artifacts import AnimationRunStore
from calendar_anim.calendar.recurrence_validation.models import (
    ValidationResourcePlan,
    ValidationResourceRole,
)
from calendar_anim.calendar.subcolumn_ordering import (
    SubcolumnOrderStrategy,
    serialize_summary_key,
    summary_order_keys,
)
from calendar_anim.exceptions import CalendarAnimError

ORDERING_VALIDATION_ID = "recurrence-zero-width-ordering-account-b-01"
ORDERING_START_WEEK = date(2030, 4, 7)
ORDERING_COLOR_IDS = ("1", "3", "2", "4")


class OrderingValidationWeek(BaseModel):
    variant: str = Field(pattern=r"^(recurring|standalone)$")
    occurrence_index: int = Field(ge=0, le=2)
    week_start: date
    expected_events: int = 18


class RecurrenceOrderingValidationPlan(BaseModel):
    schema_version: str = "1.0"
    validation_id: str
    source_run_id: str
    calendar_profile: str = "account-b"
    calendar_name: str
    timezone: str
    weeks: list[OrderingValidationWeek]
    resources: list[ValidationResourcePlan]
    summaries: list[str]
    color_ids: list[str]
    expected_events_insert_calls: int = 36
    recurring_parent_count: int = 18
    recurring_instance_count: int = 3
    standalone_count: int = 18
    browser_zoom_percent: int = 90
    visible_window: str = "06:00-00:00"
    google_calendar_writes: bool = False

    @model_validator(mode="after")
    def validate_exact_scope(self) -> "RecurrenceOrderingValidationPlan":
        recurring = [
            item for item in self.resources if item.role is ValidationResourceRole.RECURRING_PARENT
        ]
        standalone = [
            item
            for item in self.resources
            if item.role is ValidationResourceRole.STANDALONE_CONTROL
        ]
        if len(recurring) != 18 or len(standalone) != 18 or len(self.resources) != 36:
            raise ValueError("ordering validation requires 18 parents and 18 controls")
        if self.summaries != summary_order_keys(18, SubcolumnOrderStrategy.ZERO_WIDTH):
            raise ValueError("ordering validation must use the approved 18 zero-width keys")
        if len(self.weeks) != 4 or len({item.week_start for item in self.weeks}) != 4:
            raise ValueError("ordering validation requires three recurring weeks and one control")
        for slot in range(18):
            pair = [
                item for item in self.resources if item.private_metadata["slot_index"] == str(slot)
            ]
            if len(pair) != 2:
                raise ValueError(f"slot {slot} must have one parent and one control")
            if {
                (item.summary, item.color_id, item.start.timetz(), item.end - item.start)
                for item in pair
            }.__len__() != 1:
                raise ValueError(f"slot {slot} recurring/control properties differ")
        return self

    @property
    def first_week(self) -> date:
        return min(item.week_start for item in self.weeks)

    @property
    def last_week(self) -> date:
        return max(item.week_start for item in self.weeks)

    @property
    def recurring_weeks(self) -> list[date]:
        return [item.week_start for item in self.weeks if item.variant == "recurring"]

    @property
    def standalone_week(self) -> date:
        return next(item.week_start for item in self.weeks if item.variant == "standalone")


class OrderingDomEvent(BaseModel):
    slot_index: int = Field(ge=0, le=17)
    summary: str
    summary_codepoints: str
    color_id_expected: str
    x: float
    width: float = Field(gt=0)
    y: float
    height: float = Field(gt=0)
    css_background_color: str
    rendered_color: str | None = None
    rendered_color_source: str | None = None
    element_border_colors: list[str] = Field(default_factory=list)
    descendant_background_colors: list[str] = Field(default_factory=list)
    css_custom_properties: dict[str, str] = Field(default_factory=dict)
    inline_style: str = ""
    pseudo_background_colors: list[str] = Field(default_factory=list)


class OrderingDomSnapshot(BaseModel):
    label: str
    week_start: date
    events: list[OrderingDomEvent]
    summaries_preserved: bool
    strictly_increasing_x: bool
    slot_order: list[int]


class ColorMappingVerification(StrEnum):
    YES = "YES"
    NO = "NO"
    UNKNOWN = "UNKNOWN"


class OrderingColorComparison(BaseModel):
    slot_index: int = Field(ge=0, le=17)
    expected_color_id: str
    recurring_rendered_color: str | None
    standalone_rendered_color: str | None
    match: bool


class OrderingCaptureResult(BaseModel):
    schema_version: str = "1.1"
    validation_id: str
    calendar_profile: str
    browser_zoom_percent: int
    visible_window: str
    snapshots: list[OrderingDomSnapshot]
    summaries_preserved_18_of_18: bool
    strict_x_ordering: bool
    recurring_equals_standalone: bool
    refresh_stable: bool
    navigation_stable: bool
    rendered_colors_match: bool = False
    expected_color_mapping_verified: ColorMappingVerification = ColorMappingVerification.UNKNOWN
    color_comparisons: list[OrderingColorComparison] = Field(default_factory=list)
    rendered_color_evidence: str = "legacy-wrapper-background"
    # Backward-compatible alias retained for existing consumers and artifacts.
    color_preserved: bool
    no_visible_text_pollution: bool
    result: str = Field(pattern=r"^(PASS|NO-GO)$")
    comparison_path: str
    calendar_writes: bool = False


def _event_id(validation_id: str, role: str, slot: int, start: datetime) -> str:
    canonical = json.dumps(
        {"validation_id": validation_id, "role": role, "slot": slot, "start": start.isoformat()},
        sort_keys=True,
        separators=(",", ":"),
    )
    return "ro" + hashlib.sha256(canonical.encode()).hexdigest()


def _rdate(timezone: str, starts: list[datetime]) -> str:
    values = ",".join(item.strftime("%Y%m%dT%H%M%S") for item in starts)
    return f"RDATE;TZID={timezone}:{values}"


def build_ordering_validation_plan(
    store: AnimationRunStore,
    *,
    validation_id: str = ORDERING_VALIDATION_ID,
    source_run_id: str,
    start_week: date = ORDERING_START_WEEK,
    calendar_profile: str = "account-b",
    calendar_name: str = "Calendar Animation Lab B",
) -> RecurrenceOrderingValidationPlan:
    source = store.load_plan(source_run_id)
    if start_week.weekday() != 6:
        raise CalendarAnimError("--start-week must be a Sunday")
    if source.subcolumn_order_strategy is not SubcolumnOrderStrategy.ZERO_WIDTH:
        raise CalendarAnimError("source run is not locked to zero-width ordering")
    summaries = summary_order_keys(18, SubcolumnOrderStrategy.ZERO_WIDTH)
    if source.subcolumn_order_keys != summaries:
        raise CalendarAnimError("source run does not contain the approved 18 ordering keys")
    timezone = source.timezone
    zone = ZoneInfo(timezone)
    recurring_weeks = [start_week + timedelta(weeks=index) for index in range(3)]
    standalone_week = start_week + timedelta(weeks=3)
    duration = timedelta(hours=2, minutes=15)
    metadata = {
        "generated_by": "calendar-anim-recurrence-validation",
        "validation_id": validation_id,
        "calendar_profile": calendar_profile,
        "source_run_id": source_run_id,
        "subcolumn_order_strategy": "zero-width",
        "validation_kind": "simultaneous-ordering",
    }
    resources: list[ValidationResourcePlan] = []
    colors: list[str] = []
    for slot, summary in enumerate(summaries):
        color_id = ORDERING_COLOR_IDS[slot % len(ORDERING_COLOR_IDS)]
        colors.append(color_id)
        starts = [datetime.combine(week, time(6), zone) for week in recurring_weeks]
        resources.append(
            ValidationResourcePlan(
                event_id=_event_id(validation_id, "recurring", slot, starts[0]),
                role=ValidationResourceRole.RECURRING_PARENT,
                week_start=recurring_weeks[0],
                start=starts[0],
                end=starts[0] + duration,
                summary=summary,
                color_id=color_id,
                timezone=timezone,
                recurrence=[_rdate(timezone, starts[1:])],
                private_metadata={
                    **metadata,
                    "validation_role": "recurring-parent",
                    "slot_index": str(slot),
                    "summary_codepoints": serialize_summary_key(summary),
                },
            )
        )
        control_start = datetime.combine(standalone_week, time(6), zone)
        resources.append(
            ValidationResourcePlan(
                event_id=_event_id(validation_id, "standalone", slot, control_start),
                role=ValidationResourceRole.STANDALONE_CONTROL,
                week_start=standalone_week,
                start=control_start,
                end=control_start + duration,
                summary=summary,
                color_id=color_id,
                timezone=timezone,
                private_metadata={
                    **metadata,
                    "validation_role": "standalone-control",
                    "slot_index": str(slot),
                    "summary_codepoints": serialize_summary_key(summary),
                },
            )
        )
    weeks = [
        OrderingValidationWeek(variant="recurring", occurrence_index=index, week_start=week)
        for index, week in enumerate(recurring_weeks)
    ]
    weeks.append(
        OrderingValidationWeek(variant="standalone", occurrence_index=0, week_start=standalone_week)
    )
    return RecurrenceOrderingValidationPlan(
        validation_id=validation_id,
        source_run_id=source_run_id,
        calendar_profile=calendar_profile,
        calendar_name=calendar_name,
        timezone=timezone,
        weeks=weeks,
        resources=resources,
        summaries=summaries,
        color_ids=colors,
    )


def _write_atomic(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()
    return path


class OrderingValidationStore:
    def __init__(self, root: Path = Path("output/recurrence-validation")) -> None:
        self.root = root

    def directory(self, validation_id: str) -> Path:
        return self.root / validation_id

    def plan_path(self, validation_id: str) -> Path:
        return self.directory(validation_id) / "ordering-plan.json"

    def state_path(self, validation_id: str) -> Path:
        return self.directory(validation_id) / "upload-state.json"

    def upload_report_path(self, validation_id: str) -> Path:
        return self.directory(validation_id) / "upload-report.json"

    def cleanup_report_path(self, validation_id: str) -> Path:
        return self.directory(validation_id) / "cleanup-report.json"

    def capture_directory(self, validation_id: str) -> Path:
        return self.directory(validation_id) / "captures"

    def save_plan(self, plan: RecurrenceOrderingValidationPlan) -> Path:
        path = self.plan_path(plan.validation_id)
        if path.exists() and self.load_plan(plan.validation_id) != plan:
            raise CalendarAnimError(f"Ordering validation plan already differs: {path}")
        _write_atomic(path, plan.model_dump_json(indent=2) + "\n")
        _write_atomic(
            self.directory(plan.validation_id) / "validation-report.txt", build_plan_report(plan)
        )
        return path

    def load_plan(self, validation_id: str) -> RecurrenceOrderingValidationPlan:
        path = self.plan_path(validation_id)
        try:
            return RecurrenceOrderingValidationPlan.model_validate_json(
                path.read_text(encoding="utf-8")
            )
        except FileNotFoundError as error:
            raise CalendarAnimError(f"Ordering validation plan does not exist: {path}") from error
        except (OSError, ValidationError, ValueError) as error:
            raise CalendarAnimError(f"Invalid ordering validation plan: {path}") from error

    def save_state(self, state: object) -> Path:
        from calendar_anim.calendar.recurrence_validation.models import ValidationUploadState

        assert isinstance(state, ValidationUploadState)
        state.updated_at = datetime.now(UTC)
        text = state.model_dump_json(indent=2) + "\n"
        _write_atomic(self.state_path(state.validation_id), text)
        return _write_atomic(self.upload_report_path(state.validation_id), text)

    def load_state(self, validation_id: str):  # type: ignore[no-untyped-def]
        from calendar_anim.calendar.recurrence_validation.models import ValidationUploadState

        path = self.state_path(validation_id)
        if not path.exists():
            return None
        return ValidationUploadState.model_validate_json(path.read_text(encoding="utf-8"))

    def save_cleanup(self, result: object) -> Path:
        from calendar_anim.calendar.recurrence_validation.models import ValidationCleanupResult

        assert isinstance(result, ValidationCleanupResult)
        return _write_atomic(
            self.cleanup_report_path(result.validation_id), result.model_dump_json(indent=2) + "\n"
        )

    def screenshot_path(self, validation_id: str, label: str) -> Path:
        return self.capture_directory(validation_id) / f"{label}.png"

    def capture_report_path(self, validation_id: str) -> Path:
        return self.capture_directory(validation_id) / "ordering-result.json"

    def save_capture_result(self, result: OrderingCaptureResult) -> Path:
        return _write_atomic(
            self.capture_report_path(result.validation_id),
            result.model_dump_json(indent=2) + "\n",
        )

    def comparison_path(self, validation_id: str) -> Path:
        return self.capture_directory(validation_id) / "recurring-vs-standalone-ordering.png"


def build_plan_report(plan: RecurrenceOrderingValidationPlan) -> str:
    lines = [
        "RECURRENCE ZERO-WIDTH ORDERING VALIDATION",
        "=========================================",
        "",
        f"Validation ID: {plan.validation_id}",
        f"Profile: {plan.calendar_profile}",
        f"Calendar: {plan.calendar_name}",
        f"Weeks: {plan.first_week} through {plan.last_week}",
        "Recurring parents: 18",
        "Recurring instances: 54 (18 x 3 weeks)",
        "Standalone controls: 18",
        "Expected events.insert calls: 36",
        f"Browser zoom: {plan.browser_zoom_percent}%",
        f"Visible window: {plan.visible_window}",
        "",
        "Slots",
        "-----",
    ]
    lines.extend(
        f"{slot:02d}: {serialize_summary_key(summary)} colorId={plan.color_ids[slot]}"
        for slot, summary in enumerate(plan.summaries)
    )
    lines.extend(["", "Google Calendar writes during preparation: NO", ""])
    return "\n".join(lines)


def compose_ordering_comparison(
    plan: RecurrenceOrderingValidationPlan, store: OrderingValidationStore
) -> Path:
    paths = [
        store.screenshot_path(plan.validation_id, "recurring-initial"),
        store.screenshot_path(plan.validation_id, "standalone"),
    ]
    images: list[Image.Image] = []
    try:
        for path in paths:
            with Image.open(path) as source:
                images.append(source.convert("RGB"))
        width = max(item.width for item in images)
        height = max(item.height for item in images)
        canvas = Image.new("RGB", (2 * width, height + 32), "#202124")
        draw = ImageDraw.Draw(canvas)
        for index, (label, image) in enumerate(
            zip(("RECURRING", "STANDALONE"), images, strict=True)
        ):
            draw.text((index * width + 8, 9), label, fill="white")
            canvas.paste(image, (index * width, 32))
        output = store.comparison_path(plan.validation_id)
        output.parent.mkdir(parents=True, exist_ok=True)
        canvas.save(output)
        canvas.close()
        return output
    finally:
        for image in images:
            image.close()


def analyze_snapshots(
    plan: RecurrenceOrderingValidationPlan,
    snapshots: list[OrderingDomSnapshot],
    comparison: Path,
    rendered_colors_by_label: dict[str, list[str | None]] | None = None,
    color_evidence_source: str = "dom-computed-visible-layer",
) -> OrderingCaptureResult:
    by_label = {item.label: item for item in snapshots}
    required = {"recurring-initial", "recurring-refresh", "recurring-navigation", "standalone"}
    missing = required - by_label.keys()
    if missing:
        raise CalendarAnimError(f"Missing ordering snapshots: {', '.join(sorted(missing))}")
    all_preserved = all(item.summaries_preserved for item in snapshots)
    strict = all(item.strictly_increasing_x for item in snapshots)
    expected_order = list(range(18))
    same_order = all(item.slot_order == expected_order for item in snapshots)

    def geometry(label: str) -> list[tuple[int, float, float, float, float]]:
        events = sorted(by_label[label].events, key=lambda item: item.slot_index)
        origin = min(item.x for item in events)
        return [
            (item.slot_index, item.x - origin, item.width, item.y, item.height) for item in events
        ]

    def close(
        left: list[tuple[int, float, float, float, float]],
        right: list[tuple[int, float, float, float, float]],
    ) -> bool:
        return len(left) == len(right) and all(
            a[0] == b[0] and all(abs(x - y) <= 2.0 for x, y in zip(a[1:], b[1:], strict=True))
            for a, b in zip(left, right, strict=True)
        )

    initial = geometry("recurring-initial")
    refresh = close(initial, geometry("recurring-refresh"))
    navigation = close(initial, geometry("recurring-navigation"))
    equivalent = close(initial, geometry("standalone"))
    rendered_colors_by_label = rendered_colors_by_label or {}

    def snapshot_colors(label: str) -> list[str | None]:
        supplied = rendered_colors_by_label.get(label)
        if supplied is not None:
            if len(supplied) != 18:
                raise CalendarAnimError(f"Rendered color audit for {label} must contain 18 slots")
            return supplied
        by_slot = {item.slot_index: item for item in by_label[label].events}
        return [effective_rendered_color(by_slot.get(slot)) for slot in range(18)]

    recurring_colors = snapshot_colors("recurring-initial")
    standalone_colors = snapshot_colors("standalone")
    color_comparisons = [
        OrderingColorComparison(
            slot_index=slot,
            expected_color_id=plan.color_ids[slot],
            recurring_rendered_color=recurring_colors[slot],
            standalone_rendered_color=standalone_colors[slot],
            match=recurring_colors[slot] is not None
            and recurring_colors[slot] == standalone_colors[slot],
        )
        for slot in range(18)
    ]
    rendered_colors_match = all(item.match for item in color_comparisons)
    expected_mapping = verify_expected_color_mapping(plan, color_comparisons)
    invisible = all(
        not any(character.isprintable() for character in summary) for summary in plan.summaries
    )
    passed = all(
        (
            all_preserved,
            strict,
            same_order,
            refresh,
            navigation,
            equivalent,
            rendered_colors_match,
            invisible,
        )
    )
    return OrderingCaptureResult(
        validation_id=plan.validation_id,
        calendar_profile=plan.calendar_profile,
        browser_zoom_percent=plan.browser_zoom_percent,
        visible_window=plan.visible_window,
        snapshots=snapshots,
        summaries_preserved_18_of_18=all_preserved,
        strict_x_ordering=strict and same_order,
        recurring_equals_standalone=equivalent,
        refresh_stable=refresh,
        navigation_stable=navigation,
        rendered_colors_match=rendered_colors_match,
        expected_color_mapping_verified=expected_mapping,
        color_comparisons=color_comparisons,
        rendered_color_evidence=color_evidence_source,
        color_preserved=rendered_colors_match,
        no_visible_text_pollution=invisible,
        result="PASS" if passed else "NO-GO",
        comparison_path=str(comparison),
    )


TRANSPARENT_COLORS = {"", "rgba(0, 0, 0, 0)", "transparent"}
RGB_PATTERN = re.compile(r"^rgba?\((\d+),\s*(\d+),\s*(\d+)")


def effective_rendered_color(event: OrderingDomEvent | None) -> str | None:
    if event is None:
        return None
    for value in (event.rendered_color, event.css_background_color):
        if value and value not in TRANSPARENT_COLORS:
            return value
    return None


def verify_expected_color_mapping(
    plan: RecurrenceOrderingValidationPlan,
    comparisons: list[OrderingColorComparison],
) -> ColorMappingVerification:
    mappings: dict[str, set[str]] = {}
    for item in comparisons:
        if not item.match or item.recurring_rendered_color is None:
            continue
        mappings.setdefault(item.expected_color_id, set()).add(item.recurring_rendered_color)
    expected_ids = set(plan.color_ids)
    if set(mappings) != expected_ids:
        return ColorMappingVerification.UNKNOWN
    if any(len(colors) != 1 for colors in mappings.values()):
        return ColorMappingVerification.NO
    return ColorMappingVerification.YES


def extract_rendered_slot_colors(
    screenshot: Path,
    snapshots: list[OrderingDomSnapshot],
    *,
    slot_count: int = 18,
) -> list[str | None]:
    """Recover equal-width rendered slot colors from an existing screenshot.

    The palette is learned from opaque chip colors already present in the DOM capture.
    The screenshot, rather than the wrapper style, is then the source of truth.
    """

    palette_names = {
        color
        for snapshot in snapshots
        for event in snapshot.events
        for color in (event.rendered_color, event.css_background_color)
        if color and color not in TRANSPARENT_COLORS and _rgb_tuple(color) is not None
    }
    palette: dict[tuple[int, int, int], str] = {}
    for name in palette_names:
        rgb = _rgb_tuple(name)
        if rgb is not None:
            palette[rgb] = name
    if not palette:
        return [None] * slot_count
    with Image.open(screenshot) as source:
        image = source.convert("RGB")
        column_counts: dict[int, Counter[tuple[int, int, int]]] = {}
        for x in range(image.width):
            column_palette_counts: Counter[tuple[int, int, int]] = Counter(
                pixel
                for pixel in (image.getpixel((x, y)) for y in range(image.height))
                if pixel in palette
            )
            if sum(column_palette_counts.values()) >= 10:
                column_counts[x] = column_palette_counts
        if not column_counts:
            return [None] * slot_count
        left, right = min(column_counts), max(column_counts) + 1
        lane_width = (right - left) / slot_count
        colors: list[str | None] = []
        for slot in range(slot_count):
            start = round(left + slot * lane_width)
            end = round(left + (slot + 1) * lane_width)
            lane_counts: Counter[tuple[int, int, int]] = Counter()
            for x in range(start, end):
                lane_counts.update(column_counts.get(x, Counter()))
            colors.append(palette[lane_counts.most_common(1)[0][0]] if lane_counts else None)
        return colors


def _rgb_tuple(value: str) -> tuple[int, int, int] | None:
    match = RGB_PATTERN.match(value)
    if match is None:
        return None
    red, green, blue = (int(part) for part in match.groups())
    return red, green, blue
