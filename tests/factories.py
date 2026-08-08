from calendar_anim.calendar.calibration.models import CalibrationProfile
from calendar_anim.models.animation import (
    AnimationManifest,
    AnimationStatistics,
    RenderInfo,
    SourceInfo,
)
from calendar_anim.models.frame import AnimationFrame, Block


def make_manifest(block: Block | None = None) -> AnimationManifest:
    blocks = [block or Block(x=0, y=0, width=2, color_id="0", color_hex="#000000")]
    return AnimationManifest(
        animation_id="test-animation",
        source=SourceInfo(file_name="tiny.avi", start_seconds=0, duration_seconds=1, source_fps=5),
        render=RenderInfo(
            frame_count=1,
            output_fps=1,
            grid_width=4,
            grid_height=4,
            fit="contain",
            palette="grayscale",
            colors=2,
            background=None,
            background_tolerance=30,
        ),
        statistics=AnimationStatistics(non_empty_pixels=2, blocks=1, estimated_events=1),
        frames=[
            AnimationFrame(
                index=0, timestamp_seconds=0, image="frames/frame_000.png", blocks=blocks
            )
        ],
    )


def make_ready_calibration_profile() -> CalibrationProfile:
    return CalibrationProfile.model_validate(
        {
            "calendar_ui": {
                "timezone": "America/Sao_Paulo",
                "visible_start_hour": 6,
                "visible_end_hour": 18,
            },
            "vertical_mapping": {
                "minimum_visible_event_minutes": 5,
                "minimum_distinguishable_height_minutes": 30,
            },
            "horizontal_mapping": {
                "maximum_tested_overlap_columns": 6,
                "usable_overlap_columns_per_day": 6,
                "days_used": 7,
            },
            "color_mapping": {
                "tested_color_ids": [str(value) for value in range(1, 12)],
                "preferred_color_ids": [str(value) for value in range(1, 12)],
                "recommended_color_count": 11,
            },
            "position_mapping": {
                "week_alignment_ok": True,
                "timezone_alignment_ok": True,
                "day_alignment_ok": True,
                "vertical_alignment_ok": True,
                "week_starts_on": "sunday",
            },
            "horizontal_bar_mapping": {
                "independent_cells_appear_contiguous": True,
                "visible_gaps_between_cells": False,
                "same_color_cells_merge_visually": True,
                "maximum_useful_bar_width": 6,
                "recommended_horizontal_strategy": "independent-cells",
            },
            "subcolumn_order_mapping": {
                "forward_visual_order": [0, 1, 2, 3, 4, 5],
                "reverse_visual_order": [5, 4, 3, 2, 1, 0],
                "shuffled_visual_order": [2, 5, 0, 4, 1, 3],
                "stable_after_refresh": True,
                "stable_after_navigation": True,
                "stable_after_reopen": True,
                "creation_order_controls_layout": False,
                "recommended_slot_order_strategy": "summary-prefix",
                "factor_tested": True,
                "controlling_property": "summary",
                "factor_stable": True,
            },
        }
    )
