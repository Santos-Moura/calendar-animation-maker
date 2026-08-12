import os
from datetime import UTC, datetime
from pathlib import Path

from PIL import Image, ImageDraw
from pydantic import ValidationError

from calendar_anim.calendar.recurrence_validation.models import (
    RecurrenceValidationPlan,
    ValidationCleanupResult,
    ValidationUploadState,
)
from calendar_anim.exceptions import CalendarAnimError


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


class RecurrenceValidationStore:
    def __init__(self, root: Path = Path("output/recurrence-validation")) -> None:
        self.root = root

    def directory(self, validation_id: str) -> Path:
        return self.root / validation_id

    def plan_path(self, validation_id: str) -> Path:
        return self.directory(validation_id) / "validation-plan.json"

    def state_path(self, validation_id: str) -> Path:
        return self.directory(validation_id) / "upload-state.json"

    def report_path(self, validation_id: str) -> Path:
        return self.directory(validation_id) / "validation-report.txt"

    def upload_report_path(self, validation_id: str) -> Path:
        return self.directory(validation_id) / "upload-report.json"

    def cleanup_report_path(self, validation_id: str) -> Path:
        return self.directory(validation_id) / "cleanup-report.json"

    def capture_directory(self, validation_id: str) -> Path:
        return self.directory(validation_id) / "captures"

    def screenshot_path(self, validation_id: str, pair_index: int, variant: str) -> Path:
        return self.capture_directory(validation_id) / f"pair-{pair_index}-{variant}.png"

    def comparison_path(self, validation_id: str) -> Path:
        return self.capture_directory(validation_id) / "recurring-vs-standalone.png"

    def save_plan(self, plan: RecurrenceValidationPlan) -> Path:
        path = self.plan_path(plan.validation_id)
        serialized = plan.model_dump_json(indent=2) + "\n"
        if path.exists():
            existing = self.load_plan(plan.validation_id)
            if existing != plan:
                raise CalendarAnimError(f"Validation plan already differs: {path}")
            return path
        _write_atomic(path, serialized)
        _write_atomic(self.report_path(plan.validation_id), build_plan_report(plan))
        return path

    def load_plan(self, validation_id: str) -> RecurrenceValidationPlan:
        path = self.plan_path(validation_id)
        try:
            return RecurrenceValidationPlan.model_validate_json(path.read_text(encoding="utf-8"))
        except FileNotFoundError as error:
            raise CalendarAnimError(f"Validation plan does not exist: {path}") from error
        except (OSError, ValidationError, ValueError) as error:
            raise CalendarAnimError(f"Invalid validation plan: {path}") from error

    def save_state(self, state: ValidationUploadState) -> Path:
        state.updated_at = datetime.now(UTC)
        serialized = state.model_dump_json(indent=2) + "\n"
        _write_atomic(self.state_path(state.validation_id), serialized)
        return _write_atomic(self.upload_report_path(state.validation_id), serialized)

    def load_state(self, validation_id: str) -> ValidationUploadState | None:
        path = self.state_path(validation_id)
        if not path.exists():
            return None
        try:
            return ValidationUploadState.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValidationError, ValueError) as error:
            raise CalendarAnimError(f"Invalid validation upload state: {path}") from error

    def save_cleanup(self, result: ValidationCleanupResult) -> Path:
        return _write_atomic(
            self.cleanup_report_path(result.validation_id),
            result.model_dump_json(indent=2) + "\n",
        )


def build_plan_report(plan: RecurrenceValidationPlan) -> str:
    parent = plan.resources[0]
    lines = [
        "RECURRENCE SMALLEST REAL VALIDATION",
        "===================================",
        "",
        f"Validation ID: {plan.validation_id}",
        f"Source: {plan.source_run_id}, frame {plan.source_frame_index}, "
        f"event {plan.source_event_index}",
        f"Summary: {plan.visual_properties.summary_codepoints}",
        f"colorId: {plan.visual_properties.color_id}",
        f"Time: {plan.visual_properties.local_start_time}",
        f"Duration: {plan.visual_properties.duration_seconds} seconds",
        f"Timezone: {plan.timezone}",
        "",
        "Scope",
        "-----",
        "Recurring resources: 1 parent",
        "Recurring displayed instances: 3 (DTSTART + 2 RDATE values)",
        "Standalone control resources: 3",
        "Displayed weeks: 6",
        "Expected events.insert calls on a fresh run: 4",
        f"RDATE: {parent.recurrence[0]}",
        "",
        "Weeks",
        "-----",
    ]
    for week in sorted(plan.weeks, key=lambda item: (item.pair_index, item.variant)):
        lines.append(f"Pair {week.pair_index}: {week.variant:<10} {week.week_start}")
    lines.extend(
        [
            "",
            "Safety",
            "------",
            "Plan generation performs no Calendar call.",
            "Upload preflight refuses any unrelated event in the six weeks.",
            "Cleanup filters generated_by + validation_id and never uses source run metadata.",
            "Google Calendar writes: NO",
            "",
        ]
    )
    return "\n".join(lines)


def compose_comparison(
    plan: RecurrenceValidationPlan,
    store: RecurrenceValidationStore,
) -> Path:
    images: list[Image.Image] = []
    try:
        for pair_index in range(3):
            for variant in ("recurring", "standalone"):
                path = store.screenshot_path(plan.validation_id, pair_index, variant)
                if not path.is_file():
                    raise CalendarAnimError(f"Missing validation screenshot: {path}")
                with Image.open(path) as source:
                    images.append(source.convert("RGB"))
        width = max(image.width for image in images)
        height = max(image.height for image in images)
        label_height = 30
        comparison = Image.new("RGB", (width * 2, (height + label_height) * 3), "#202124")
        draw = ImageDraw.Draw(comparison)
        for pair_index in range(3):
            for column, variant in enumerate(("recurring", "standalone")):
                image = images[pair_index * 2 + column]
                week = next(
                    item
                    for item in plan.weeks
                    if item.pair_index == pair_index and item.variant == variant
                )
                x = column * width
                y = pair_index * (height + label_height)
                label = f"Pair {pair_index + 1} | {variant.upper()} | week {week.week_start}"
                draw.text((x + 8, y + 8), label, fill="white")
                comparison.paste(image, (x, y + label_height))
        output = store.comparison_path(plan.validation_id)
        output.parent.mkdir(parents=True, exist_ok=True)
        comparison.save(output)
        comparison.close()
        return output
    finally:
        for image in images:
            image.close()
