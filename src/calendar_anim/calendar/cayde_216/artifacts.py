import json
import os
from pathlib import Path

from calendar_anim.calendar.cayde_216.models import (
    Cayde216RemotePreflight,
    Cayde216SizingReport,
)
from calendar_anim.calendar.multi_frame.artifacts import AnimationRunStore
from calendar_anim.calendar.multi_frame.models import MultiFramePlan
from calendar_anim.calendar.recurrence_compaction.models import (
    RecurrenceMigrationPlan,
    RecurrenceStudyReport,
)


def write_atomic(path: Path, text: str) -> Path:
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


class Cayde216Store(AnimationRunStore):
    def __init__(self, root: Path = Path("output/216-plans")) -> None:
        super().__init__(root)

    def recurrence_plan_path(self, run_id: str) -> Path:
        return self.run_directory(run_id) / "recurrence-plan.json"

    def recurrence_report_path(self, run_id: str) -> Path:
        return self.run_directory(run_id) / "recurrence-report.json"

    def sizing_report_path(self, run_id: str) -> Path:
        return self.run_directory(run_id) / "sizing-report.json"

    def final_plan_path(self, run_id: str) -> Path:
        return self.run_directory(run_id) / "final-plan.txt"

    def future_capture_plan_path(self, run_id: str) -> Path:
        return self.run_directory(run_id) / "future-capture-plan.json"

    def future_media_plan_path(self, run_id: str) -> Path:
        return self.run_directory(run_id) / "future-media-plan.json"

    def preflight_path(self, run_id: str) -> Path:
        return self.run_directory(run_id) / "remote-preflight.json"

    def preflight_text_path(self, run_id: str) -> Path:
        return self.run_directory(run_id) / "remote-preflight.txt"

    def save_planning_artifacts(
        self,
        plan: MultiFramePlan,
        recurrence: RecurrenceMigrationPlan,
        study: RecurrenceStudyReport,
        sizing: Cayde216SizingReport,
    ) -> list[Path]:
        capture_plan = {
            "schema_version": "1.0",
            "run_id": plan.run_id,
            "strategy": "single-profile-account-b",
            "profile": "account-b",
            "zoom_percent": 90,
            "frames": [
                {
                    "human_frame": frame.frame_index + 1,
                    "frame_index": frame.frame_index,
                    "week_start": frame.week_start.isoformat(),
                    "expected_occurrences": frame.planned_events,
                    "source_frame_plan": str(
                        self.frame_directory(plan, frame.frame_index) / "frame-plan.json"
                    ),
                }
                for frame in plan.frames
            ],
            "preview_human_frames": [1, 54, 108, 162, 216],
            "mode": "header_preserved_fill",
            "resolution": "1512x864",
            "left_time_gutter": True,
            "header": True,
            "vertical_interval": "06:00-00:00",
            "pre_06_blank_gap": False,
            "readiness": {
                "correct_week": True,
                "correct_view": True,
                "structural_grid": True,
                "visual_content_occupancy_when_expected": True,
                "stable_screenshot": True,
                "empty_expected-content_capture": "retry-without-checkpoint",
            },
        }
        media_plan = {
            "schema_version": "1.0",
            "run_id": plan.run_id,
            "frames": 216,
            "fps": 6,
            "duration_seconds": 36.0,
            "input_sequence": "frame_000.png-frame_215.png",
            "resolution": "1512x864",
            "video": {
                "codec": "H.264",
                "profile": "High",
                "crf": 10,
                "preset": "slow",
                "pixel_format": "yuv420p",
                "sar": "1:1",
            },
            "audio": {
                "source": "input.mp4",
                "clip_start_seconds": 114.0,
                "clip_end_seconds": 150.0,
                "video_stream_copy": True,
                "audio_codec": "AAC",
                "max_av_delta_seconds": 0.05,
            },
        }
        payloads = {
            "recurrence-plan.json": recurrence.model_dump_json(indent=2) + "\n",
            "recurrence-report.json": study.model_dump_json(indent=2) + "\n",
            "sizing-report.json": sizing.model_dump_json(indent=2) + "\n",
            "final-plan.txt": sizing_text(sizing),
            "future-capture-plan.json": json.dumps(capture_plan, indent=2) + "\n",
            "future-media-plan.json": json.dumps(media_plan, indent=2) + "\n",
        }
        return [
            write_atomic(self.run_directory(plan.run_id) / name, payload)
            for name, payload in payloads.items()
        ]

    def save_preflight(self, report: Cayde216RemotePreflight) -> Path:
        path = write_atomic(
            self.preflight_path(report.run_id), report.model_dump_json(indent=2) + "\n"
        )
        write_atomic(
            self.preflight_text_path(report.run_id),
            "\n".join(
                [
                    "CAYDE 216 REMOTE PREFLIGHT",
                    "==========================",
                    "",
                    f"Profile: {report.profile}",
                    f"Account: {report.authenticated_account}",
                    f"Calendar: {report.calendar_name}",
                    f"Access role: {report.access_role}",
                    f"Timezone: {report.timezone}",
                    f"Range: {report.range_start} -> {report.range_end_exclusive} (exclusive)",
                    f"Unexpected events: {report.unexpected_event_count}",
                    f"New range clean: {'YES' if report.new_range_clean else 'NO'}",
                    f"Old artifacts unchanged: {'YES' if report.old_artifacts_unchanged else 'NO'}",
                    "Old resources touched: NO",
                    "Google Calendar reads: YES",
                    "Google Calendar writes: NO",
                    f"Result: {report.result}",
                    "",
                ]
            ),
        )
        return path

    def save_json_report(self, path: Path, payload: dict[str, object]) -> Path:
        return write_atomic(path, json.dumps(payload, indent=2) + "\n")


def sizing_text(report: Cayde216SizingReport) -> str:
    def duration(seconds: float) -> str:
        rounded = round(seconds)
        hours, remainder = divmod(rounded, 3600)
        minutes, seconds = divmod(remainder, 60)
        return f"{hours}h {minutes:02d}m {seconds:02d}s"

    return "\n".join(
        [
            "CAYDE FINAL 216F / 6FPS PLAN",
            "============================",
            "",
            "Source",
            "------",
            f"Clip: {report.clip_start_seconds:.1f}s -> {report.clip_end_seconds:.1f}s",
            f"FPS: {report.fps:g}",
            f"Frames: {report.frame_count}",
            f"Duration: {report.duration_seconds:.1f}s",
            "",
            "Old 108 Version",
            "---------------",
            f"Weeks: {report.old_first_week} -> {report.old_last_week}",
            f"Parents: {report.old_account_b_parents}",
            f"Logical occurrences: {report.old_logical_occurrences}",
            "Touched: NO",
            "",
            "New 216 Version",
            "---------------",
            f"Frame 1 week: {report.first_week}",
            f"Frame 216 week: {report.last_week}",
            f"Week count: {report.week_count}",
            f"Overlap: {report.old_week_overlap}",
            f"Logical occurrences: {report.logical_occurrences}",
            f"Unique signatures: {report.unique_recurrence_signatures}",
            f"Parents: {report.recurring_parents}",
            f"Reduction: {report.reduction_percent:.3f}%",
            f"Singleton parents: {report.singleton_parents}",
            f"Largest group: {report.largest_group}",
            f"Largest RDATE count: {report.largest_rdate_count}",
            "",
            "Expansion",
            "---------",
            f"Missing: {report.expansion_missing}",
            f"Extra: {report.expansion_extra}",
            f"Duplicates: {report.expansion_duplicates}",
            f"Exact equality: {'YES' if report.expansion_exact else 'NO'}",
            "",
            "IDs",
            "---",
            f"Unique: {'YES' if report.parent_ids_unique else 'NO'}",
            f"Collisions with existing B: {report.parent_id_collisions_with_existing_b}",
            "",
            "Payload",
            "-------",
            f"Min: {report.payload.minimum_bytes} bytes",
            f"Mean: {report.payload.mean_bytes:.1f} bytes",
            f"p95: {report.payload.p95_bytes} bytes",
            f"Max: {report.payload.maximum_bytes} bytes",
            "",
            "ETA",
            "---",
            *[
                f"@{interval}s/write: {duration(report.eta_seconds[interval])}"
                for interval in ("0.75", "1.0", "1.5", "2.0")
            ],
            "",
            "Comparison vs 108",
            "-----------------",
            f"Logical ratio: {report.logical_occurrence_ratio:.4f}x",
            f"Parent ratio: {report.parent_count_ratio:.4f}x",
            f"Upload ETA ratio: {report.upload_eta_ratio:.4f}x",
            "",
            "Capture",
            "-------",
            "Profile: account-b",
            "Zoom: 90%",
            "Frames: 1-216",
            "Mode: header_preserved_fill",
            "Resolution: 1512x864",
            f"Readiness protection: {report.readiness_protection}",
            "",
            "OLD 108 VERSION TOUCHED: NO",
            "OLD FRAME WEEKS OVERLAP: 0",
            "OLD PARENT IDs TOUCHED: 0",
            "OLD FINAL OUTPUTS TOUCHED: NO",
            "Google Calendar writes: NO",
            "",
            "NEXT STEP",
            "=========",
            "Review sizing.",
            "Do NOT upload yet.",
            "",
        ]
    )
