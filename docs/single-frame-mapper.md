# Single Frame Calendar Mapper

The single-frame experiment asks whether one processed frame remains recognizable after it is translated into normal Google Calendar events. It does not upload an animation: one selected manifest frame becomes one planned Calendar week.

## Frame-to-week model

The mapper reads the calibrated grid instead of hardcoding it. With the current measurements:

```text
6 subcolumns/day x 7 days = 42 columns
06:00-18:00 / 30 minutes = 24 rows
candidate grid = 42x24
```

For a Sunday-first profile:

```text
x=0..5   -> Sunday, logical subcolumns 0..5
x=6..11  -> Monday, logical subcolumns 0..5
...
x=36..41 -> Saturday, logical subcolumns 0..5

y=0      -> 06:00-06:30
...
y=23     -> 17:30-18:00
```

`--start-date` may be any date inside the target week. The mapper normalizes it to the recorded `week_starts_on`.

## Mapping modes

### `sparse`

Sparse is the backward-compatible CLI default.

```text
manifest blocks
    -> expanded foreground matrix
    -> contain fitting
    -> foreground events only
```

It minimizes event count. Its limitation is horizontal: Google Calendar has no API field named `subcolumn`, so three isolated simultaneous events may be rendered as three equal columns even when the intended logical positions were 0, 3, and 5.

### `full-grid`

Full-grid is the recommended baseline for the first real visual experiment.

```text
manifest blocks
    -> expanded foreground matrix
    -> contain fitting
    -> complete target canvas
    -> foreground + structural background events
```

Every target cell exists in the logical canvas. In the uncompressed baseline, each cell becomes
one event. Structural background cells occupy otherwise empty slots, making the geometry more
predictable.

This does not create an API-level subcolumn property. Events are emitted deterministically in `day -> row -> subcolumn` order, while final visual ordering still belongs to Google Calendar and must be inspected in the real UI.

## Summary-based subcolumn ordering

The mapper still emits events deterministically with the submission key:

```text
(day_offset, logical_y, subcolumn_index)
```

Creation order was not reliable in the real Calendar UI, and `colorId` must remain free to represent the video. The full-grid mapper therefore derives a second key from the logical subcolumn:

```text
subcolumn 0 -> summary "00"
subcolumn 1 -> summary "01"
subcolumn 2 -> summary "02"
subcolumn 3 -> summary "03"
subcolumn 4 -> summary "04"
subcolumn 5 -> summary "05"
```

Foreground and structural background cells use exactly the same key for a given subcolumn. The key does not depend on color, row, role, source block, or frame content. `frame-plan.json` records `summary-prefix` and all six keys; `mapping-report.txt` prints an auditable row sample such as:

```text
day_offset=1 row=4
subcolumn=0 summary="00" colorId=8 role=background
subcolumn=1 summary="01" colorId=8 role=background
subcolumn=2 summary="02" colorId=5 role=foreground
subcolumn=3 summary="03" colorId=8 role=background
subcolumn=4 summary="04" colorId=3 role=foreground
subcolumn=5 summary="05" colorId=8 role=background
```

Google Calendar has no `subcolumn` API field and does not document summary-based overlap ordering as a layout contract. The strategy is based on stable behavior observed in this project and is isolated so it can be replaced. Short technical titles may appear over the event blocks; invisible Unicode, CSS, and DOM workarounds are not used in this baseline.

Sparse mode deliberately keeps the legacy blank summary. Without structural fillers it still cannot promise absolute positions, even if a summary key were added.

## Source background versus structural background

These are different concepts:

- source background is content removed from the video before the manifest is written;
- Calendar structural background is an intentional event that occupies a full-grid cell.

Structural events carry `cell_role=background`; visible pixels carry `cell_role=foreground`. Both carry private `generated_by`, `animation_id`, `run_id`, `frame_index`, `logical_x`, `logical_y`, `day_offset`, `subcolumn`, `subcolumn_index`, and `subcolumn_order_strategy` metadata. Summary ordering additionally records `subcolumn_order_key`. Foreground events retain `source_block_index`.

Full-grid event summaries contain `00..05`; sparse summaries retain one blank space.

## Aspect ratio and background color

Only `contain` fitting is implemented. It preserves the source aspect ratio, centers the image, and fills every remaining target cell in full-grid mode.

The Calendar API supports a fixed event palette rather than arbitrary RGB. Full-grid therefore uses a Calendar `colorId` for its canvas:

```powershell
--calendar-background-color-id 8
```

When omitted, the deterministic project default is `8`. Foreground RGB still passes through nearest-color and contrast mapping; the selected structural background does not. Calendar may render the same color differently in light and dark themes, so the frame plan is theme-independent and the final appearance requires visual comparison.

## Dry-run commands

Production default (`full-grid` plus synchronized bands):

```powershell
python -m calendar_anim calendar map-frame .\output\primeiro-teste\animation.json --frame 0 --start-date 2026-11-22 --run-id primeiro-frame-bands-01
```

Explicit uncompressed baseline:

```powershell
python -m calendar_anim calendar map-frame .\output\primeiro-teste\animation.json --frame 0 --mapping-mode full-grid --event-compression none --calendar-background-color-id 8 --start-date 2026-09-07 --run-id primeiro-frame-baseline-01
```

Sparse diagnostics require both `--mapping-mode sparse --event-compression none`.

Dry-run is fully local: it does not construct a Google gateway, authenticate, write a token, create a calendar, create an event, or delete an event.

## Artifacts and metrics

```text
output/frame-mapping/<run_id>/
|-- frame-plan.json
|-- mapping-report.txt
|-- source-frame.png
|-- mapped-preview.png
|-- mapped-debug.png
`-- execution-result.json
```

- `source-frame.png` is the processed manifest image;
- `mapped-preview.png` is a solid logical pixel canvas, not Calendar CSS;
- `mapped-debug.png` adds rows, subcolumns, and day boundaries;
- `mapping-report.txt` includes both mode estimates, ordering strategy, slot keys, the empirical-behavior warning, and a row sample;
- `frame-plan.json` records mode, background, roles, metrics, ordering strategy, keys, events, and metadata.

The report distinguishes expanded source cells, fitted foreground, structural background, total cells, foreground/background events, foreground colors, and execution limits.

For the tested frame 0:

```text
Source grid: 28x20
Target grid: 42x24
Expanded source cells: 26
Foreground cells after fitting: 32

Sparse events: 32
Full-grid background cells: 976
Full-grid events: 1008
```

## Cost of fidelity

The uncompressed full-grid fallback performs no filler optimization:

```text
42x24 = 1008 events/frame
1008 x 12 frames = 12096 events
1008 x 60 frames = 60480 events
```

These totals are planning estimates, not a claim that Google Calendar will safely accept those workloads. The baseline prioritizes visual fidelity over event economy.

### Synchronized horizontal-band compression (production default)

New plans use the strategy automatically. It may also be selected explicitly with:

```powershell
--event-compression synchronized-horizontal-bands
```

For each day, consecutive rows merge only when their complete six-slot vectors have identical
Calendar colors and foreground/background roles. Each band still creates six simultaneous events
with summaries `00..05`; all six share the same start and end. Day boundaries are never crossed.
The `42x24` mapped canvas and its previews remain unchanged while the persisted event drafts and
upload counts become smaller. `--event-compression none` preserves the original one-event-per-cell
behavior.

Real compressed execution additionally requires the synchronized-band calibration to be recorded
as stable and safe in the loaded profile. Production validation passed with six real frames:
792/792 events were created, capture and GIF composition completed, and manual visual equivalence
with the 6,048-event baseline passed. This 86.9% reduction is sample-specific.

`--event-compression none` remains supported for fallback, baseline, and debug plans. Existing
persisted plans retain their recorded strategy; legacy plans without the field load as `none`.

## Real upload safety

The normal single-frame execution limit is 1200 events and the absolute configurable ceiling remains 2000. Dry-run is never blocked by the normal limit; real execution is blocked before authentication when the plan exceeds it.

Real execution additionally requires:

- explicit `--execute`;
- explicit `--start-date`;
- a complete calibration profile, including stable summary-order evidence matching the mapper capability;
- confirmation defaulting to `N`, unless `--yes` is supplied;
- the recognized secondary `Calendar Animation Lab`;
- no existing frame with the same run metadata.

The confirmation discloses mapping mode, subcolumn strategy, target grid, foreground events, background events, total events, calendar, frame, and run ID.

```powershell
python -m calendar_anim calendar map-frame .\output\primeiro-teste\animation.json --frame 0 --mapping-mode full-grid --calendar-background-color-id 8 --start-date 2026-09-07 --run-id primeiro-frame-full-grid-01 --execute
```

Partial failures preserve planned, created, failed, foreground-created, background-created, IDs, and errors in `execution-result.json`.

Cleanup removes foreground and background together because both share the same run metadata:

```powershell
python -m calendar_anim calendar cleanup --animation-id primeiro-teste --run-id primeiro-frame-full-grid-01
python -m calendar_anim calendar cleanup --animation-id primeiro-teste --run-id primeiro-frame-full-grid-01 --execute
```

## Multi-frame orchestration

The single-frame mapper remains the only source of truth for fit, full-grid background, color mapping, `summary-prefix`, metadata, and event generation. The multi-frame layer calls it once per selected manifest frame, assigns consecutive weeks, and persists an immutable animation plan plus mutable frame checkpoints.

It does not copy or replace the mapper. See [multi-frame upload](multi-frame-upload.md) for `plan-animation`, resumable serial upload, partial-frame recovery, and animation cleanup.

## Why there is no Playwright yet

Calendar API writes are not visually atomic. Multi-frame upload now pre-creates frames in separate weeks with frame-level checkpoints. Browser navigation, stabilization detection, screenshots, and MP4/GIF composition remain intentionally separate future work.

## Roadmap

```text
full-grid single frame
    -> record stable summary ordering evidence
    -> generate summary-prefix keys 00..05
    -> compare one full-grid frame with real Calendar
    -> validate shape fidelity
    -> multi-frame planner and frame-level resume
    -> real 6-frame test
    -> manual week navigation
    -> Playwright capture
    -> final MP4/GIF
    -> future performance optimization
```

Sparse remains a valid future optimization. A hybrid strategy may eventually retain fillers only where they are structurally required, but it is deliberately outside this baseline.
