from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from calendar_anim.calendar.calibration.models import CalibrationObservations, CalibrationProfile

DEFAULT_PROFILE_PATH = Path("output/calibration/calibration-profile.yaml")


def load_profile(path: Path) -> CalibrationProfile:
    if not path.exists():
        return CalibrationProfile()
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return CalibrationProfile.model_validate(raw)


def save_profile(profile: CalibrationProfile, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = profile.model_dump(mode="json", exclude_none=False)
    path.write_text(
        yaml.safe_dump(payload, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    return path


def load_observations(path: Path) -> CalibrationObservations:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return CalibrationObservations.model_validate(raw)


def apply_observations(
    profile: CalibrationProfile,
    recorded: CalibrationObservations,
) -> CalibrationProfile:
    ui = recorded.calendar_ui
    for field in (
        "view",
        "timezone",
        "browser_zoom_percent",
        "viewport_width",
        "viewport_height",
        "sidebar_visible",
        "weekends_visible",
        "visible_start_hour",
        "visible_end_hour",
    ):
        value = getattr(ui, field)
        if value is not None:
            setattr(profile.calendar_ui, field, value)

    values = recorded.observations
    if values.minimum_visible_event_minutes is not None:
        profile.vertical_mapping.minimum_visible_event_minutes = (
            values.minimum_visible_event_minutes
        )
    if values.minimum_distinguishable_height_minutes is not None:
        profile.vertical_mapping.minimum_distinguishable_height_minutes = (
            values.minimum_distinguishable_height_minutes
        )
    if values.maximum_tested_overlap_columns is not None:
        profile.horizontal_mapping.maximum_tested_overlap_columns = (
            values.maximum_tested_overlap_columns
        )
    if values.usable_overlap_columns is not None:
        profile.horizontal_mapping.usable_overlap_columns_per_day = values.usable_overlap_columns
    if values.tested_color_ids is not None:
        profile.color_mapping.tested_color_ids = values.tested_color_ids
    if values.preferred_color_ids is not None:
        profile.color_mapping.preferred_color_ids = values.preferred_color_ids
    if values.recommended_color_count is not None:
        profile.color_mapping.recommended_color_count = values.recommended_color_count
    if values.poor_contrast_color_ids is not None:
        profile.color_mapping.poor_contrast_color_ids = values.poor_contrast_color_ids
    if values.similar_color_groups is not None:
        profile.color_mapping.similar_color_groups = values.similar_color_groups

    for field in (
        "week_alignment_ok",
        "timezone_alignment_ok",
        "day_alignment_ok",
        "vertical_alignment_ok",
        "week_starts_on",
    ):
        value = getattr(values, field)
        if value is not None:
            setattr(profile.position_mapping, field, value)
    if recorded.pattern == "position-grid":
        profile.position_mapping.visible_start_hour = ui.visible_start_hour
        profile.position_mapping.visible_end_hour = ui.visible_end_hour

    for field in (
        "independent_cells_appear_contiguous",
        "visible_gaps_between_cells",
        "same_color_cells_merge_visually",
        "maximum_useful_bar_width",
        "partial_bar_positioning_predictable",
        "recommended_horizontal_strategy",
    ):
        value = getattr(values, field)
        if value is not None:
            setattr(profile.horizontal_bar_mapping, field, value)

    slot_order_fields = {
        "visual_order_forward": "forward_visual_order",
        "visual_order_reverse": "reverse_visual_order",
        "visual_order_shuffled": "shuffled_visual_order",
        "stable_after_refresh": "stable_after_refresh",
        "stable_after_navigation": "stable_after_navigation",
        "stable_after_reopen": "stable_after_reopen",
        "creation_order_controls_layout": "creation_order_controls_layout",
        "recommended_slot_order_strategy": "recommended_slot_order_strategy",
        "ordering_factor_tested": "factor_tested",
        "ordering_controlling_property": "controlling_property",
        "ordering_factor_stable": "factor_stable",
    }
    for observation_field, profile_field in slot_order_fields.items():
        value = getattr(values, observation_field)
        if value is not None:
            setattr(profile.subcolumn_order_mapping, profile_field, value)

    if values.vertical_compression is not None:
        profile.vertical_compression = values.vertical_compression
    if values.synchronized_horizontal_bands is not None:
        profile.synchronized_horizontal_bands = values.synchronized_horizontal_bands

    if recorded.pattern == "color-palette" and values.notes:
        profile.color_mapping.notes = values.notes
    elif recorded.pattern == "position-grid" and values.notes:
        profile.position_mapping.notes = values.notes
    elif recorded.pattern == "horizontal-bars" and values.notes:
        profile.horizontal_bar_mapping.notes = values.notes
    elif recorded.pattern == "subcolumn-order" and values.notes:
        profile.subcolumn_order_mapping.notes = values.notes

    # Re-validate so the derived row/column counts are refreshed after mutation.
    return CalibrationProfile.model_validate(profile.model_dump())


def profile_summary(profile: CalibrationProfile) -> str:
    ui = profile.calendar_ui
    vertical = profile.vertical_mapping
    horizontal = profile.horizontal_mapping
    colors = profile.color_mapping
    position = profile.position_mapping
    bars = profile.horizontal_bar_mapping
    slot_order = profile.subcolumn_order_mapping
    vertical_compression = profile.vertical_compression
    synchronized_bands = profile.synchronized_horizontal_bands
    vertical_pending = "pending calibration"
    control_height = vertical_pending
    control_equivalent = vertical_pending
    mixed_slots = vertical_pending
    staggered_stable = vertical_pending
    compression_acceptable = vertical_pending
    compression_safe = vertical_pending
    if vertical_compression is not None:
        control_height = _yes_no(vertical_compression.control_vs_compressed.same_total_height)
        control_equivalent = _yes_no(vertical_compression.control_vs_compressed.visually_equivalent)
        mixed_slots = _yes_no(vertical_compression.fixed_start_mixed_duration.slot_order_preserved)
        staggered_stable = _yes_no(vertical_compression.staggered.overlap_layout_stable)
        compression_acceptable = _yes_no(vertical_compression.conclusion.visually_acceptable)
        compression_safe = _yes_no(vertical_compression.conclusion.safe_for_mapper)

    synchronized_pending = "pending calibration"
    synchronized_widths = synchronized_pending
    synchronized_order = synchronized_pending
    synchronized_boundaries = synchronized_pending
    synchronized_acceptable = synchronized_pending
    synchronized_safe = synchronized_pending
    if synchronized_bands is not None:
        synchronized_widths = _yes_no(synchronized_bands.equal_widths_preserved)
        synchronized_order = _yes_no(synchronized_bands.slot_order_preserved)
        synchronized_boundaries = _yes_no(synchronized_bands.adjacent_boundaries_stable)
        synchronized_acceptable = _yes_no(synchronized_bands.visually_acceptable)
        synchronized_safe = _yes_no(synchronized_bands.safe_for_mapper)

    viewport = "pending"
    if ui.viewport_width is not None and ui.viewport_height is not None:
        viewport = f"{ui.viewport_width}x{ui.viewport_height}"
    zoom = f"{ui.browser_zoom_percent}%" if ui.browser_zoom_percent is not None else "pending"
    color_status = _color_status(profile)
    position_status = _position_status(profile)
    bar_status = _bar_status(profile)

    lines = [
        "Calendar Calibration Summary",
        "============================",
        "",
        "UI",
        f"  View: {ui.view}",
        f"  Timezone: {ui.timezone}",
        f"  Zoom: {zoom}",
        f"  Viewport: {viewport}",
        (
            "  Visible window: "
            f"{ui.visible_start_hour:02d}:00-{ui.visible_end_hour:02d}:00 "
            f"({(ui.visible_end_hour - ui.visible_start_hour) * 60} minutes)"
        ),
        "",
        "Vertical mapping",
        f"  Minimum visible event: {_minutes(vertical.minimum_visible_event_minutes)}",
        (
            "  Minimum distinguishable height: "
            f"{_minutes(vertical.minimum_distinguishable_height_minutes)}"
        ),
        f"  Logical rows: {_value(vertical.logical_rows)}",
        "",
        "Horizontal mapping",
        (f"  Maximum tested overlap columns: {_value(horizontal.maximum_tested_overlap_columns)}"),
        (
            "  Usable overlaps per day: "
            f"{_horizontal_value(horizontal.usable_overlap_columns_per_day)}"
        ),
        f"  Days used: {horizontal.days_used}",
        f"  Logical columns: {_logical_columns(horizontal.logical_columns)}",
        "",
        "Color mapping",
        f"  Status: {color_status}",
        f"  Tested color IDs: {_list_or_pending(colors.tested_color_ids)}",
        f"  Preferred color IDs: {_list_or_pending(colors.preferred_color_ids)}",
        f"  Recommended color count: {_value(colors.recommended_color_count)}",
        f"  Poor contrast color IDs: {_list_or_none(colors.poor_contrast_color_ids)}",
        f"  Similar color groups: {_groups_or_none(colors.similar_color_groups)}",
        "",
        "Position mapping",
        f"  Status: {position_status}",
        f"  Week alignment: {_alignment(position.week_alignment_ok)}",
        f"  Timezone alignment: {_alignment(position.timezone_alignment_ok)}",
        f"  Day alignment: {_alignment(position.day_alignment_ok)}",
        f"  Vertical alignment: {_alignment(position.vertical_alignment_ok)}",
        f"  Week starts on: {_value(position.week_starts_on)}",
        "",
        "Horizontal bar mapping",
        f"  Status: {bar_status}",
        (f"  Recommended strategy: {_value(bars.recommended_horizontal_strategy)}"),
        (
            "  Independent cells appear contiguous: "
            f"{_yes_no(bars.independent_cells_appear_contiguous)}"
        ),
        f"  Visible gaps between cells: {_yes_no(bars.visible_gaps_between_cells)}",
        (f"  Same-color cells merge visually: {_yes_no(bars.same_color_cells_merge_visually)}"),
        f"  Maximum useful bar width: {_value(bars.maximum_useful_bar_width)}",
        (f"  Partial positioning predictable: {_yes_no(bars.partial_bar_positioning_predictable)}"),
        "",
        "Subcolumn order mapping",
        f"  Status: {slot_order.status}",
        f"  Forward creation order: {_slot_order(slot_order.forward_creation_order)}",
        f"  Forward visual order: {_slot_order(slot_order.forward_visual_order)}",
        f"  Reverse creation order: {_slot_order(slot_order.reverse_creation_order)}",
        f"  Reverse visual order: {_slot_order(slot_order.reverse_visual_order)}",
        f"  Shuffled visual order: {_slot_order(slot_order.shuffled_visual_order)}",
        f"  Stable after refresh: {_yes_no(slot_order.stable_after_refresh)}",
        f"  Stable after navigation: {_yes_no(slot_order.stable_after_navigation)}",
        f"  Stable after reopen: {_yes_no(slot_order.stable_after_reopen)}",
        (f"  Creation order controls layout: {_yes_no(slot_order.creation_order_controls_layout)}"),
        (f"  Recommended slot strategy: {_value(slot_order.recommended_slot_order_strategy)}"),
        f"  Ordering factor tested: {_yes_no(slot_order.factor_tested)}",
        f"  Controlling property: {_value(slot_order.controlling_property)}",
        f"  Ordering factor stable: {_yes_no(slot_order.factor_stable)}",
        (
            "  Mapper supports recommended strategy: "
            f"{_yes_no(slot_order.recommended_strategy_supported)}"
        ),
        "",
        "Vertical event compression experiment",
        f"  Status: {_vertical_compression_status(vertical_compression)}",
        f"  Control/compressed same height: {control_height}",
        f"  Control/compressed visually equivalent: {control_equivalent}",
        f"  Mixed durations preserve slots: {mixed_slots}",
        f"  Staggered overlaps remain stable: {staggered_stable}",
        f"  Visually acceptable: {compression_acceptable}",
        f"  Safe for production mapper: {compression_safe}",
        "",
        "Synchronized horizontal bands experiment",
        f"  Status: {_synchronized_bands_status(synchronized_bands)}",
        f"  Equal widths preserved: {synchronized_widths}",
        f"  Slot order preserved: {synchronized_order}",
        f"  Adjacent boundaries stable: {synchronized_boundaries}",
        f"  Visually acceptable: {synchronized_acceptable}",
        f"  Safe for production mapper: {synchronized_safe}",
        "",
        (
            "Candidate logical grid: "
            f"{_candidate_grid(profile.candidate_grid.width, profile.candidate_grid.height)}"
        ),
        "",
        f"Mapper readiness: {profile.mapper_readiness}",
    ]
    if profile.missing_mapper_calibrations:
        lines.extend(["", "Missing:"])
        lines.extend(f"- {calibration}" for calibration in profile.missing_mapper_calibrations)
    return "\n".join(lines)


def _minutes(value: int | None) -> str:
    return f"{value} minutes" if value is not None else "pending"


def _value(value: Any | None) -> str:
    return str(value) if value is not None else "pending"


def _horizontal_value(value: int | None) -> str:
    return str(value) if value is not None else "not measured yet"


def _logical_columns(value: int | None) -> str:
    return str(value) if value is not None else "pending overlap-columns calibration"


def _candidate_grid(columns: int | None, rows: int | None) -> str:
    if columns is None or rows is None:
        return "pending"
    return f"{columns}x{rows}"


def _list_or_pending(values: list[str]) -> str:
    return ", ".join(values) if values else "pending calibration"


def _list_or_none(values: list[str]) -> str:
    return ", ".join(values) if values else "none recorded"


def _groups_or_none(groups: list[list[str]]) -> str:
    if not groups:
        return "none recorded"
    return "; ".join(",".join(group) for group in groups)


def _alignment(value: bool | None) -> str:
    if value is None:
        return "pending calibration"
    return "OK" if value else "NOT OK"


def _yes_no(value: bool | None) -> str:
    if value is None:
        return "pending calibration"
    return "yes" if value else "no"


def _slot_order(value: list[int] | None) -> str:
    if value is None:
        return "pending calibration"
    return ",".join(str(slot) for slot in value)


def _vertical_compression_status(value: Any | None) -> str:
    if value is None:
        return "pending vertical-compression calibration"
    conclusion = value.conclusion
    if conclusion.visually_acceptable is not None and conclusion.safe_for_mapper is not None:
        return "recorded"
    return "incomplete vertical-compression calibration"


def _synchronized_bands_status(value: Any | None) -> str:
    if value is None:
        return "pending synchronized-horizontal-bands calibration"
    if value.visually_acceptable is not None and value.safe_for_mapper is not None:
        return "recorded"
    return "incomplete synchronized-horizontal-bands calibration"


def _color_status(profile: CalibrationProfile) -> str:
    mapping = profile.color_mapping
    required = (
        bool(mapping.tested_color_ids),
        bool(mapping.preferred_color_ids),
        mapping.recommended_color_count is not None,
    )
    return _completion_status(required, "color-palette")


def _position_status(profile: CalibrationProfile) -> str:
    mapping = profile.position_mapping
    required = (
        mapping.week_alignment_ok is not None,
        mapping.timezone_alignment_ok is not None,
        mapping.day_alignment_ok is not None,
        mapping.vertical_alignment_ok is not None,
        mapping.week_starts_on is not None,
    )
    return _completion_status(required, "position-grid")


def _bar_status(profile: CalibrationProfile) -> str:
    mapping = profile.horizontal_bar_mapping
    required = (
        mapping.independent_cells_appear_contiguous is not None,
        mapping.visible_gaps_between_cells is not None,
        mapping.same_color_cells_merge_visually is not None,
        mapping.maximum_useful_bar_width is not None,
        mapping.recommended_horizontal_strategy is not None,
    )
    return _completion_status(required, "horizontal-bars")


def _completion_status(required: tuple[bool, ...], calibration: str) -> str:
    if all(required):
        return "recorded"
    if any(required):
        return f"incomplete {calibration} calibration"
    return f"pending {calibration} calibration"
