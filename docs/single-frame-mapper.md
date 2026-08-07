# Single Frame Calendar Mapper

The single-frame experiment answers one question before the project scales up: can one processed video frame remain recognizable after it is translated into normal Google Calendar events?

It does not upload an animation. It selects exactly one manifest frame, creates a local plan and comparison images, and optionally uploads only that frame to the dedicated laboratory calendar.

## Frame-to-week model

One future animation frame occupies one Calendar week. The calibrated UI starts on Sunday, so the logical horizontal axis is:

```text
x=0..5   -> Sunday, subcolumns 0..5
x=6..11  -> Monday, subcolumns 0..5
...
x=36..41 -> Saturday, subcolumns 0..5
```

`--start-date` may be any date inside the intended week. The mapper normalizes it to the calibrated `week_starts_on`. For example, `2026-09-07` resolves to Sunday `2026-09-06`.

The vertical axis is derived from the profile rather than hardcoded. With the current measurements, 06:00-18:00 and 30-minute distinguishable rows yield 24 rows:

```text
y=0  -> 06:00-06:30
y=1  -> 06:30-07:00
...
y=23 -> 17:30-18:00
```

The current candidate capacity is `42x24`, but the mapper always reads it from the calibration profile.

## Mapping stages

```text
ManifestBlock
    -> LogicalCell
    -> contain fitting
    -> CalendarMappedCell
    -> CalendarEventDraft
```

A source block with `width=4` is expanded to four unit cells before fitting. This prevents the mapper from claiming that `block.width` can be represented by one Calendar event. The first experiment deliberately uses one mapped cell per event; fidelity is more important than event-count optimization.

Only `contain` is implemented. It preserves source aspect ratio, centers the fitted image in the target grid, and leaves unused target cells empty. Background pixels were already removed before the manifest was written, so absent cells do not produce events.

## Color mapping

Each source `color_hex` is compared with the calibrated usable Calendar palette. The mapper chooses the nearest RGB color and applies a centralized contrast fallback against the manifest background, or the dark Calendar background when the manifest has no explicit background.

The mapper handles pixels, colors, contrast, shape, and position. It does not recognize semantic objects. Temporal color stability is reserved for multi-frame planning.

## Important horizontal limitation

Google Calendar has no API field named `subcolumn`. It decides horizontal placement from simultaneous events. The plan records the intended day and subcolumn and creates same-row cells with simultaneous start/end times, but the real UI must still be inspected.

Sparse positions are especially experimental: Calendar can widen or reorder overlapping events because it owns the layout algorithm. `mapped-preview.png` is the mapper's logical interpretation, not a simulation of Calendar CSS.

This is why dry-run is allowed while the horizontal-bar observation is pending, but `--execute` is blocked until the consolidated profile reports `READY FOR SINGLE-FRAME EXPERIMENT`.

## Dry-run

```powershell
python -m calendar_anim calendar map-frame .\output\primeiro-teste\animation.json --frame 0 --start-date 2026-09-07 --run-id frame-test-001
```

Use an explicit profile when needed:

```powershell
python -m calendar_anim calendar map-frame .\output\primeiro-teste\animation.json --frame 0 --calibration-profile .\output\calibration\calibration-profile.yaml --start-date 2026-09-07 --run-id frame-test-001
```

Dry-run is fully local: it does not construct a Google gateway, authenticate, write a token, create a calendar, create an event, or delete an event.

## Artifacts and metrics

```text
output/frame-mapping/<run_id>/
|-- frame-plan.json
|-- mapping-report.txt
|-- source-frame.png
|-- mapped-preview.png
`-- execution-result.json
```

The report exposes:

- frame index;
- source and target grids;
- source blocks;
- expanded and non-background cells;
- mapped cells;
- Calendar event count;
- unique Calendar colors;
- cells per event;
- compression ratio;
- execute limit and warnings.

Compare `source-frame.png` and `mapped-preview.png` side by side before considering a real upload. Do not hide a high event count: this version intentionally starts at one event per mapped cell.

## Real upload safety

The default single-frame execute limit is 500 events and the absolute configurable ceiling is 2000. Dry-run still produces its plan when the event count exceeds the chosen execute limit; real upload fails before authentication.

Real upload additionally requires:

- explicit `--execute`;
- explicit `--start-date`;
- a complete calibration profile;
- event count within the configured limit;
- user confirmation, unless `--yes` is supplied;
- the recognized secondary `Calendar Animation Lab`;
- no existing events with the same `generated_by`, `animation_id`, `run_id`, and `frame_index`.

```powershell
python -m calendar_anim calendar map-frame .\output\primeiro-teste\animation.json --frame 0 --start-date 2026-09-07 --run-id frame-test-001 --execute
```

Every event records `generated_by`, `animation_id`, `run_id`, `frame_index`, `logical_x`, `logical_y`, `subcolumn`, and `source_block_index` as private metadata. Partial failures preserve created IDs, created/failed counts, and errors in `execution-result.json`.

Cleanup remains dry-run by default:

```powershell
python -m calendar_anim calendar cleanup --animation-id primeiro-teste --run-id frame-test-001
python -m calendar_anim calendar cleanup --animation-id primeiro-teste --run-id frame-test-001 --execute
```

Use the actual `animation_id` shown by `map-frame`; it may differ from the output-directory name.

## Why there is no Playwright yet

Calendar API writes are not visually atomic. The final animation will not create and delete events while recording. The chosen architecture is:

```text
pre-upload every frame
    -> one frame per week
    -> wait for Calendar to stabilize
    -> capture that week
    -> advance and capture the next week
    -> compose screenshots into MP4/GIF
```

This branch does not implement multiple real frames, batching, retry/resume, Playwright, real screenshots, or MP4/GIF composition.

## Roadmap

```text
Single Frame Mapper
    -> 2-3 dry-run frames in consecutive weeks
    -> multi-frame planner
    -> guarded full upload
    -> retry/resume
    -> Playwright stable waits and screenshots
    -> MP4/GIF composition
```
