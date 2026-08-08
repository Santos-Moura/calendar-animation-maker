# Multi-frame Calendar upload

## Purpose

The multi-frame layer proves animation flow without changing the validated visual mapper:

```text
animation.json
    -> MultiFramePlan
    -> existing SingleFrameMapper for each selected frame
    -> one consecutive Calendar week per frame
    -> serial frame upload
    -> atomic frame-level checkpoint
```

Reliability and resumability are more important than upload speed. The implementation does not use the batch API, async workers, aggressive concurrency, event compression, or event-level resume.

## Frame and week assignment

`--start-date` accepts any day in the first selected week. The calibration profile determines the week start. With a Sunday profile and `2026-10-07`:

```text
frame 0 -> 2026-10-04
frame 1 -> 2026-10-11
frame 2 -> 2026-10-18
```

The first selected frame always occupies the first week, even when `--frame-start` is greater than zero. Frame run IDs are deterministic:

```text
animation-test-01-frame-0000
animation-test-01-frame-0001
```

Long parent run IDs receive a stable hash before the frame suffix to avoid collisions while respecting the Calendar metadata limit.

## Generate a small test manifest

Choose a simple two-second section of the existing `input.mp4`; the start time remains a user decision:

```powershell
python -m calendar_anim render .\input.mp4 --start <START_SECONDS> --duration 2 --frames 6 --width 28 --height 20 --palette grayscale --colors 4 --background "#000000" --background-tolerance 35 --output-fps 3 --output .\output\multi-frame-test
Invoke-Item .\output\multi-frame-test\preview.gif
```

These values are a testing recommendation, not hardcoded planner behavior.

## Plan locally

```powershell
python -m calendar_anim calendar plan-animation .\output\multi-frame-test\animation.json --frame-start 0 --frame-count 6 --mapping-mode full-grid --start-date 2026-10-04 --run-id animation-test-01
```

Planning never constructs a Google gateway or authenticates. It produces:

```text
output/animation-runs/animation-test-01/
|-- animation-plan.json
|-- animation-state.json
|-- animation-report.txt
`-- frames/
    |-- frame-0000/
    |   |-- frame-plan.json
    |   |-- mapping-report.txt
    |   |-- source-frame.png
    |   |-- mapped-preview.png
    |   |-- mapped-debug.png
    |   `-- execution-result.json
    `-- frame-0001/
        `-- ...
```

`animation-plan.json` is the immutable intention. Replanning the same run with different content is rejected. `animation-state.json` is mutable progress and is saved with a temporary file, flush/fsync, and atomic replace.

## State example

```json
{
  "run_id": "animation-test-01",
  "animation_id": "multi-frame-test",
  "calendar_id": "lab-calendar-id",
  "calendar_created": false,
  "frames": [
    {
      "frame_index": 0,
      "status": "completed",
      "planned_events": 1008,
      "created_events": 1008,
      "failed_events": 0,
      "errors": [],
      "frame_started_at": "2026-10-04T12:00:00Z",
      "frame_completed_at": "2026-10-04T12:09:42Z",
      "duration_seconds": 582.0
    },
    {
      "frame_index": 1,
      "status": "partial",
      "planned_events": 1008,
      "created_events": 600,
      "failed_events": 1,
      "errors": ["simulated upload failure"],
      "frame_started_at": "2026-10-11T12:00:00Z",
      "frame_completed_at": "2026-10-11T12:05:00Z",
      "duration_seconds": 300.0
    },
    {
      "frame_index": 2,
      "status": "pending",
      "planned_events": 1008,
      "created_events": 0,
      "failed_events": 0,
      "errors": [],
      "frame_started_at": null,
      "frame_completed_at": null,
      "duration_seconds": null
    }
  ],
  "updated_at": "2026-10-11T12:05:00Z"
}
```

Statuses are `pending`, `uploading`, `completed`, `partial`, and `failed`. A stale `uploading` status from an interrupted process is converted to `partial` before resume.

## Inspect an upload without Google

```powershell
python -m calendar_anim calendar upload-animation --run-id animation-test-01
```

This lists `UPLOAD`, `SKIP`, or `RECOVERY REQUIRED` per frame. It performs no OAuth, Calendar lookup, creation, or deletion.

## Real upload and resume

```powershell
python -m calendar_anim calendar upload-animation --run-id animation-test-01 --execute
python -m calendar_anim calendar upload-animation --run-id animation-test-01 --resume --execute
```

`--execute` displays frame count, total events, current progress, and a confirmation defaulting to `N`. Upload is serial and reports progress every 50 attempted events. Each completed frame is checkpointed immediately and skipped on later runs.

The normal 1200-event guard applies independently to every frame. It is not a 1200-event limit for the whole animation. A six-frame full-grid plan therefore contains approximately:

```text
1008 events/frame
6 frames
6048 total events
```

## Failure and partial recovery

The default policy stops on the first frame failure. If frame 4 fails after 600 creations:

```text
frames 0..3 -> remain completed
frame 4     -> partial, created_events=600
frame 5+    -> remain pending
```

A normal resume refuses to guess which individual events are missing. Explicit recovery deletes events tagged with frame 4's deterministic run ID, resets only frame 4, and recreates it:

```powershell
python -m calendar_anim calendar upload-animation --run-id animation-test-01 --resume --recover-partial --execute
```

The destructive recovery still requires `--execute` and confirmation. Frames 0..3 are not queried for re-upload and are never deleted. If remote events exist while local state says `pending` or `failed`, execution stops as an inconsistency instead of silently duplicating them.

Ctrl+C marks the current frame `partial`; an unexpected failure before any known creation marks it `failed`. A failed frame with no remote events may be retried. There is deliberately no `--continue-on-error` in this MVP.

## Cleanup

Preview cleanup for one frame or the entire animation without Google:

```powershell
python -m calendar_anim calendar cleanup-animation --run-id animation-test-01 --frame 2
python -m calendar_anim calendar cleanup-animation --run-id animation-test-01
```

Perform the matching deletion only after reviewing the dry-run:

```powershell
python -m calendar_anim calendar cleanup-animation --run-id animation-test-01 --frame 2 --execute
python -m calendar_anim calendar cleanup-animation --run-id animation-test-01 --execute
```

Cleanup queries exact `generated_by`, `animation_id`, `run_id`, and `frame_index` metadata in the recognized `Calendar Animation Lab`. A successful frame cleanup resets only that frame to `pending`.

## Roadmap

```text
single real full-grid frame (complete)
    -> multi-frame planner and checkpoint/resume (this feature)
    -> real six-frame test
    -> manual week navigation
    -> Playwright week capture
    -> screenshot sequence
    -> GIF / MP4
    -> performance optimization
```

Playwright is intentionally not part of this layer. Capture consumes stable, already-uploaded Calendar weeks and has different authentication, waiting, selector, and screenshot responsibilities.
