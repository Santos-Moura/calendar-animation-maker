import os
from pathlib import Path

from calendar_anim.calendar.recurrence_compaction.models import (
    RecurrenceMigrationPlan,
    RecurrenceStudyReport,
)


def write_recurrence_artifacts(
    output_directory: Path,
    plan: RecurrenceMigrationPlan,
    report: RecurrenceStudyReport,
) -> tuple[Path, Path, Path]:
    output_directory.mkdir(parents=True, exist_ok=True)
    plan_path = output_directory / "recurrence-plan.json"
    report_json_path = output_directory / "recurrence-report.json"
    report_text_path = output_directory / "recurrence-report.txt"
    _write_atomic(plan_path, plan.model_dump_json(indent=2) + "\n")
    _write_atomic(report_json_path, report.model_dump_json(indent=2) + "\n")
    _write_atomic(report_text_path, build_recurrence_report_text(report))
    return plan_path, report_json_path, report_text_path


def build_recurrence_report_text(report: RecurrenceStudyReport) -> str:
    full = report.full_scope
    distribution = report.distribution
    lines = [
        "RECURRENCE COMPACTION STUDY",
        "===========================",
        "",
        "Current model",
        "-------------",
        f"Logical/rendered occurrences: {report.rendered_instances}",
        f"Independent inserts: {report.current_independent_inserts}",
        "",
        "Signature model",
        "---------------",
        "Fields: " + ", ".join(report.signature_fields_included),
        "Excluded: " + ", ".join(report.signature_fields_excluded),
        f"Unique signatures: {report.unique_exact_signatures}",
        "",
        "Grouping",
        "--------",
        f"Singleton groups: {distribution.singleton}",
        f"2-5: {distribution.two_to_five}",
        f"6-10: {distribution.six_to_ten}",
        f"11-25: {distribution.eleven_to_twenty_five}",
        f"26-50: {distribution.twenty_six_to_fifty}",
        f"51-100: {distribution.fifty_one_to_one_hundred}",
        f">100: {distribution.over_one_hundred}",
        f"Mean: {distribution.mean:.3f}",
        f"Median: {distribution.median:.3f}",
        f"p95: {distribution.p95:.3f}",
        f"Largest: {distribution.largest}",
        "",
        "Parent counts",
        "-------------",
        f"Unlimited RDATE: {full.parents_unlimited}",
    ]
    lines.extend(f"Chunk {chunk}: {full.parents_by_chunk[chunk]}" for chunk in report.chunk_sizes)
    lines.extend(
        [
            "",
            "API reduction",
            "-------------",
            f"Unlimited: {full.reduction_by_chunk['unlimited']:.3f}%",
        ]
    )
    lines.extend(
        f"{chunk}: {full.reduction_by_chunk[str(chunk)]:.3f}%" for chunk in report.chunk_sizes
    )
    lines.extend(_scope_lines("Background", report.background))
    lines.extend(_scope_lines("Foreground", report.foreground))
    lines.extend(
        [
            "",
            "Existing run migration",
            "----------------------",
            f"Completed frames preserved: {report.completed_frames_preserved}",
            f"Partial singles preserved: {report.partial_single_events_preserved}",
            f"All existing singles preserved: {report.all_existing_single_events_preserved}",
            f"Remaining occurrences: {report.remaining_occurrences}",
            f"Migration chunk: {report.migration_parent_chunk_size}",
            f"Recurring parents required: {report.migration_parents_required}",
            f"Migration insert reduction: {report.migration_insert_reduction:.3f}%",
            f"Duplicate occurrences: {report.migration_duplicate_occurrences}/"
            f"{report.remaining_occurrences}",
            f"Largest estimated insert payload: {report.largest_migration_payload_bytes} bytes",
            f"Mean estimated insert payload: {report.mean_migration_payload_bytes:.1f} bytes",
            "",
            "Equivalence",
            "-----------",
            "Expanded full logical set equals original: "
            + _yes_no(report.expanded_full_set_equals_original),
            "Expanded migration set equals missing: "
            + _yes_no(report.migration_expansion_equals_missing),
            "Calendar UI real validation required: YES",
            "",
            "Limits and semantics",
            "--------------------",
            f"Google recurring instance limit: {report.recurring_event_instance_limit}",
            f"RDATE count limit: {report.rdate_count_limit}",
            f"Request body size limit: {report.request_body_size_limit}",
            f"recurrence[] size limit: {report.recurrence_array_size_limit}",
            "Recurring instances vs general Calendar usage-limit accounting: "
            f"{report.general_usage_limit_instance_accounting}",
            "RDATE uses local DATE-TIME values with the plan IANA timezone.",
            "Parent summary, colorId, duration, visibility, transparency, and event type are "
            "shared by all planned instances.",
            "Private Google metadata identifies the run/group/chunk; the complete parent-to-"
            "frame mapping remains in recurrence-plan.json.",
            "RRULE + EXDATE optimization: possible future study only; not used here.",
            "",
            "Quota",
            "-----",
            "Google writes performed: NO",
            "Batch solves quota: NO",
            "",
            "Recommendation",
            "--------------",
            "GO (small real validation only)" if full.reduction_by_chunk["100"] >= 50 else "NO-GO",
            "",
            "Reason:",
            _recommendation_reason(report),
            "",
            "Next smallest real validation:",
            "Create one parent with 2-3 RDATE instances in unused weeks, capture those weeks, "
            "compare against equivalent standalone events, then clean up that isolated parent.",
            "",
        ]
    )
    return "\n".join(lines)


def _scope_lines(title: str, scope: object) -> list[str]:
    from calendar_anim.calendar.recurrence_compaction.models import ScopeCompaction

    assert isinstance(scope, ScopeCompaction)
    return [
        "",
        title,
        "-" * len(title),
        f"Occurrences: {scope.occurrences}",
        f"Parents: {scope.parents_unlimited}",
        f"Reduction: {scope.reduction_by_chunk['unlimited']:.3f}%",
    ]


def _recommendation_reason(report: RecurrenceStudyReport) -> str:
    reduction = report.full_scope.reduction_by_chunk["100"]
    return (
        f"Chunk 100 reduces planned inserts by {reduction:.3f}% while the local expansion "
        "accounts for every logical occurrence exactly once. Calendar week-view rendering "
        "equivalence remains unproven and requires the small real validation."
    )


def _yes_no(value: bool) -> str:
    return "YES" if value else "NO"


def _write_atomic(path: Path, text: str) -> None:
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
