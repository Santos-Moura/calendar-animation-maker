import hashlib
import statistics
from datetime import UTC, date, datetime, timedelta
from math import ceil
from pathlib import Path

from calendar_anim.calendar.calibration.profile import DEFAULT_PROFILE_PATH, load_profile
from calendar_anim.calendar.cayde_216.artifacts import Cayde216Store
from calendar_anim.calendar.cayde_216.models import (
    Cayde216SizingReport,
    FrameOccurrenceStatistics,
    PayloadSizing,
)
from calendar_anim.calendar.event_identity import deterministic_event_id
from calendar_anim.calendar.frame_mapping.models import (
    EventCompressionMode,
    FrameMappingMode,
)
from calendar_anim.calendar.high_detail import apply_high_detail_grid
from calendar_anim.calendar.multi_frame.artifacts import initialize_animation_run
from calendar_anim.calendar.multi_frame.models import MultiFramePlan
from calendar_anim.calendar.multi_frame.planner import build_multi_frame_plan
from calendar_anim.calendar.recurrence_compaction.models import RecurrenceMigrationPlan
from calendar_anim.calendar.recurrence_compaction.planner import build_recurrence_study
from calendar_anim.calendar.subcolumn_ordering import SubcolumnOrderStrategy
from calendar_anim.exceptions import CalendarAnimError
from calendar_anim.renderer.manifest import read_manifest, validate_manifest_files

RUN_ID = "cayde-final-216f-6fps-rdate-126x72-36s-01"
SOURCE_MANIFEST_RELATIVE = Path("source-render/animation.json")
OLD_RUN_ID = "cayde-final-hybrid-rdate-126x72-3fps-36s-01"
OLD_SOURCE_RUN_ID = "cayde-final-126x72-3fps-36s-01"
OLD_PREFIX_RUN_ID = "cayde-final-b-prefix-rdate-frames-001-023-01"
OLD_FIRST_WEEK = date(2027, 10, 10)
OLD_LAST_WEEK = date(2029, 10, 28)
FIRST_WEEK = OLD_LAST_WEEK + timedelta(days=7)
FRAME_COUNT = 216
FPS = 6.0
CLIP_START = 114.0
CLIP_END = 150.0
CHUNK_SIZE = 100
MAX_EVENTS_PER_FRAME = 5200
EXPECTED_INPUT_SHA256 = "c5c94c0c1361bd0a42034f7e7419abb1aba6d2b13b1ae7af1ac44bd1e152b507"

OLD_RECURRENCE_PLAN = Path(
    "output/hybrid-plans/cayde-final-hybrid-rdate-126x72-3fps-36s-01/account-b-recurrence-plan.json"
)
OLD_PREFIX_PLAN = Path(
    "output/account-b-prefix-plans/cayde-final-b-prefix-rdate-frames-001-023-01/"
    "prefix-recurrence-plan.json"
)
OLD_PROTECTED_FILES = (
    Path("output/animation-runs") / OLD_SOURCE_RUN_ID / "animation-plan.json",
    OLD_RECURRENCE_PLAN,
    OLD_PREFIX_PLAN,
    Path("output/hybrid-runs") / OLD_RUN_ID / "account-b-upload-state.json",
    Path("output/hybrid-runs")
    / OLD_RUN_ID
    / "single-profile-final-capture-state"
    / "header_preserved_fill-1512x864.json",
    Path("output/hybrid-runs")
    / OLD_RUN_ID
    / "final"
    / "single-profile"
    / "header-preserved-fill"
    / "1512x864"
    / "final-video-no-audio.mp4",
    Path("output/hybrid-runs")
    / OLD_RUN_ID
    / "final"
    / "single-profile"
    / "header-preserved-fill"
    / "1512x864"
    / "final-with-audio.mp4",
)


def build_cayde_216_plan(
    *,
    store: Cayde216Store | None = None,
    profile_path: Path = DEFAULT_PROFILE_PATH,
    input_path: Path = Path("input.mp4"),
    generated_at: datetime | None = None,
) -> tuple[Cayde216SizingReport, list[Path]]:
    store = store or Cayde216Store()
    _validate_input(input_path)
    protected_before = protected_hashes()
    manifest_path = store.run_directory(RUN_ID) / SOURCE_MANIFEST_RELATIVE
    manifest = read_manifest(manifest_path)
    errors = validate_manifest_files(manifest, manifest_path.resolve())
    if errors:
        raise CalendarAnimError("216-frame source manifest is invalid: " + "; ".join(errors))
    _validate_manifest(manifest)
    manifest = manifest.model_copy(update={"animation_id": "cayde-final-216f-6fps"})
    profile = apply_high_detail_grid(load_profile(profile_path), "126x72")
    plan, frame_plans = build_multi_frame_plan(
        manifest,
        profile,
        frame_start=0,
        frame_count=FRAME_COUNT,
        anchor_date=FIRST_WEEK,
        run_id=RUN_ID,
        max_events_per_frame=MAX_EVENTS_PER_FRAME,
        calendar_name="Calendar Animation Lab B",
        calendar_profile="account-b",
        mapping_mode=FrameMappingMode.FULL_GRID,
        event_compression=EventCompressionMode.SYNCHRONIZED_HORIZONTAL_BANDS,
        palette_preset="cayde-final",
        subcolumn_order_strategy=SubcolumnOrderStrategy.ZERO_WIDTH,
        grid_profile="high-detail-126x72",
    )
    initialize_animation_run(plan, frame_plans, manifest, manifest_path, store)
    result = build_recurrence_study(
        plan,
        store.load_state(RUN_ID),
        store,
        migration_chunk_size=CHUNK_SIZE,
        generated_at=generated_at or datetime.now(UTC),
    )
    recurrence = result.migration_plan
    expected_keys = _expected_occurrence_keys(store, plan)
    expanded = [key for parent in recurrence.parents for key in parent.occurrence_keys]
    expanded_set = set(expanded)
    missing = len(expected_keys - expanded_set)
    extra = len(expanded_set - expected_keys)
    duplicates = len(expanded) - len(expanded_set)
    old_parents = _existing_parent_ids()
    new_parent_ids = [parent.parent_id for parent in recurrence.parents]
    payloads = sorted(parent.estimated_insert_payload_bytes for parent in recurrence.parents)
    if not payloads:
        raise CalendarAnimError("216-frame recurrence plan contains no parents")
    weeks = [frame.week_start for frame in plan.frames]
    old_weeks = {OLD_FIRST_WEEK + timedelta(weeks=index) for index in range(108)}
    protected_after = protected_hashes()
    frame_counts = sorted(plan.events_per_frame)
    parent_count = len(recurrence.parents)
    report = Cayde216SizingReport(
        run_id=RUN_ID,
        source_file="input.mp4",
        source_sha256=EXPECTED_INPUT_SHA256,
        clip_start_seconds=CLIP_START,
        clip_end_seconds=CLIP_END,
        duration_seconds=CLIP_END - CLIP_START,
        fps=FPS,
        frame_count=FRAME_COUNT,
        frame_indices=[frame.frame_index for frame in plan.frames],
        calendar_profile="account-b",
        calendar_name="Calendar Animation Lab B",
        timezone="America/Sao_Paulo",
        first_week=weeks[0],
        last_week=weeks[-1],
        week_count=len(weeks),
        all_week_deltas_seven_days=all(
            right - left == timedelta(days=7) for left, right in zip(weeks, weeks[1:], strict=False)
        ),
        old_first_week=OLD_FIRST_WEEK,
        old_last_week=OLD_LAST_WEEK,
        old_week_overlap=len(set(weeks) & old_weeks),
        logical_occurrences=plan.total_events,
        frame_occurrences=FrameOccurrenceStatistics(
            minimum=frame_counts[0],
            mean=statistics.fmean(frame_counts),
            p95=frame_counts[max(0, ceil(0.95 * len(frame_counts)) - 1)],
            maximum=frame_counts[-1],
        ),
        unique_recurrence_signatures=result.report.unique_exact_signatures,
        recurring_parents=parent_count,
        reduction_percent=result.report.migration_insert_reduction,
        singleton_parents=sum(parent.occurrence_count == 1 for parent in recurrence.parents),
        largest_group=result.report.distribution.largest,
        largest_chunk=max(parent.occurrence_count for parent in recurrence.parents),
        largest_rdate_count=max(parent.occurrence_count - 1 for parent in recurrence.parents),
        expansion_missing=missing,
        expansion_extra=extra,
        expansion_duplicates=duplicates,
        expansion_exact=(
            recurrence.expansion_equals_missing
            and missing == extra == duplicates == 0
            and len(expanded) == len(expected_keys)
        ),
        parent_ids_unique=len(new_parent_ids) == len(set(new_parent_ids)),
        parent_id_collisions_with_existing_b=len(set(new_parent_ids) & old_parents),
        existing_b_parent_count=len(old_parents),
        payload=PayloadSizing(
            minimum_bytes=payloads[0],
            mean_bytes=statistics.fmean(payloads),
            p95_bytes=payloads[max(0, ceil(0.95 * len(payloads)) - 1)],
            maximum_bytes=payloads[-1],
            within_safe_limit=payloads[-1] <= 32_000,
        ),
        eta_seconds={str(value): parent_count * value for value in (0.75, 1.0, 1.5, 2.0)},
        logical_occurrence_ratio=plan.total_events / 277_830,
        parent_count_ratio=parent_count / 46_468,
        upload_eta_ratio=parent_count / 46_468,
        readiness_protection=(
            "correct week/view + structural grid + stable screenshots + approved-palette "
            "occupancy; empty expected-content captures retry without checkpoint"
        ),
        future_preview_human_frames=[1, 54, 108, 162, 216],
        old_version_touched=protected_before != protected_after,
        old_final_outputs_touched=any(
            protected_before.get(str(path)) != protected_after.get(str(path))
            for path in OLD_PROTECTED_FILES
            if path.suffix == ".mp4"
        ),
        old_protected_sha256_before=protected_before,
        old_protected_sha256_after=protected_after,
    )
    artifacts = store.save_planning_artifacts(plan, recurrence, result.report, report)
    return report, [store.plan_path(RUN_ID), *artifacts]


def protected_hashes() -> dict[str, str]:
    hashes = {}
    for path in OLD_PROTECTED_FILES:
        if not path.is_file():
            raise CalendarAnimError(f"Protected 108-frame artifact is missing: {path}")
        hashes[str(path)] = _sha256(path)
    return hashes


def _existing_parent_ids() -> set[str]:
    plans = []
    for path in (OLD_RECURRENCE_PLAN, OLD_PREFIX_PLAN):
        try:
            plans.append(
                RecurrenceMigrationPlan.model_validate_json(path.read_text(encoding="utf-8"))
            )
        except (OSError, ValueError) as error:
            raise CalendarAnimError(f"Existing Account-B parent plan is invalid: {path}") from error
    ids = {parent.parent_id for plan in plans for parent in plan.parents}
    if len(ids) != 46_468:
        raise CalendarAnimError(f"Expected 46,468 existing Account-B parent IDs, found {len(ids)}")
    return ids


def _expected_occurrence_keys(store: Cayde216Store, plan: MultiFramePlan) -> set[str]:
    keys = set()
    for frame_index in range(FRAME_COUNT):
        frame_plan = store.load_frame_plan(plan, frame_index)
        for event in frame_plan.events:
            keys.add(f"f{frame_index:04d}:{deterministic_event_id(event)}")
    return keys


def _validate_manifest(manifest: object) -> None:
    source = manifest.source  # type: ignore[attr-defined]
    render = manifest.render  # type: ignore[attr-defined]
    expected = {
        "source file": source.file_name == "input.mp4",
        "source hash": source.sha256 == EXPECTED_INPUT_SHA256,
        "clip start": source.start_seconds == CLIP_START,
        "clip duration": source.duration_seconds == CLIP_END - CLIP_START,
        "frame count": render.frame_count == FRAME_COUNT,
        "fps": render.output_fps == FPS,
        "grid": (render.grid_width, render.grid_height) == (126, 72),
        "fit": render.fit == "contain",
        "palette": render.palette == "calendar",
        "colors": render.colors == 6,
        "background": render.background == "#000000",
        "background tolerance": render.background_tolerance == 35,
    }
    failed = [name for name, passed in expected.items() if not passed]
    if failed:
        raise CalendarAnimError("216-frame render invariants changed: " + ", ".join(failed))


def _validate_input(path: Path) -> None:
    if _sha256(path) != EXPECTED_INPUT_SHA256:
        raise CalendarAnimError("input.mp4 differs from the approved 108-frame source")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as error:
        raise CalendarAnimError(f"Could not hash protected artifact: {path}") from error
    return digest.hexdigest()
