import os
from pathlib import Path

from calendar_anim.calendar.remote_recurrence_audit.models import (
    FrameRemoteAudit,
    RemoteRecurrenceAuditReport,
)


class RemoteRecurrenceAuditStore:
    def __init__(self, root: Path = Path("output/hybrid-runs")) -> None:
        self.root = root

    def directory(self, run_id: str) -> Path:
        return self.root / run_id / "remote-recurrence-audit"

    def save(self, report: RemoteRecurrenceAuditReport) -> tuple[Path, Path]:
        directory = self.directory(report.run_id)
        for frame in report.frames:
            _write_atomic(
                directory / f"frame-{frame.human_frame:03d}.json",
                frame.model_dump_json(indent=2) + "\n",
            )
        json_path = _write_atomic(
            directory / "remote-recurrence-audit.json",
            report.model_dump_json(indent=2) + "\n",
        )
        text_path = _write_atomic(directory / "remote-recurrence-audit.txt", audit_text(report))
        return json_path, text_path


def audit_text(report: RemoteRecurrenceAuditReport) -> str:
    lines = [
        "REMOTE RECURRENCE AUDIT",
        "=======================",
        "",
        f"Run: {report.run_id}",
        f"Profile: {report.profile}",
        f"Calendar: {report.calendar_name}",
        f"Frames audited: {', '.join(str(value) for value in report.frames_audited)}",
        "",
    ]
    for frame in report.frames:
        lines.extend(_frame_text(frame))
    lines.extend(
        [
            "TOTAL",
            "=====",
            f"Expected occurrences: {report.total_expected_occurrences}",
            f"Google expanded occurrences: {report.total_google_expanded_occurrences}",
            f"Exact matches: {report.total_exact_matches}",
            f"Missing: {report.total_missing}",
            f"Extra: {report.total_extra}",
            f"Duplicates: {report.total_duplicates}",
            "",
            f"Root cause category: {report.root_cause_category}",
            f"Root cause: {report.root_cause}",
            f"Recurrence mechanism broken: {report.recurrence_mechanism_broken}",
            f"Planner/grouping wrong: {report.planner_grouping_wrong}",
            "Existing bulk salvageable without recreation: "
            f"{report.existing_bulk_salvageable_without_recreation}",
            "",
            "Google Calendar reads: YES",
            "Google Calendar writes: NO",
            "",
        ]
    )
    return "\n".join(lines)


def _frame_text(frame: FrameRemoteAudit) -> list[str]:
    lines = [
        f"FRAME {frame.human_frame}",
        "=" * (6 + len(str(frame.human_frame))),
        f"Expected occurrences: {frame.expected_occurrences}",
        f"Google-expanded occurrences: {frame.google_expanded_occurrences}",
        f"Exact matches: {frame.exact_matches}",
        f"Missing: {frame.missing}",
        f"Extra: {frame.extra}",
        f"Duplicates: {frame.duplicates}",
        f"Wrong date: {frame.wrong_date}",
        f"Wrong time: {frame.wrong_time}",
        f"Wrong summary: {frame.wrong_summary}",
        f"Wrong color: {frame.wrong_color}",
        f"Wrong parent mapping: {frame.wrong_parent_mapping}",
        "",
        "First divergences:",
    ]
    if not frame.first_divergences:
        lines.append("  none")
    for item in frame.first_divergences:
        lines.append(
            f"  {item.category}: parent={item.parent_id or 'none'} "
            f"fields={','.join(item.differing_fields) or 'n/a'}"
        )
    lines.append("")
    return lines


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
