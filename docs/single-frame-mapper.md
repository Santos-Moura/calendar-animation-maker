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

Every target cell becomes exactly one event. For every day and row, all calibrated subcolumns exist and share the same start/end interval. Structural background cells occupy otherwise empty slots, making the geometry more predictable.

This does not create an API-level subcolumn property. Events are emitted deterministically in `day -> row -> subcolumn` order, while final visual ordering still belongs to Google Calendar and must be inspected in the real UI.

## Source background versus structural background

These are different concepts:

- source background is content removed from the video before the manifest is written;
- Calendar structural background is an intentional event that occupies a full-grid cell.

Structural events carry `cell_role=background`; visible pixels carry `cell_role=foreground`. Both carry private `generated_by`, `animation_id`, `run_id`, `frame_index`, `logical_x`, `logical_y`, `subcolumn`, and `subcolumn_index` metadata. Foreground events additionally retain `source_block_index`.

Event summaries contain only one blank space so coordinates do not appear over the pixel art.

## Aspect ratio and background color

Only `contain` fitting is implemented. It preserves the source aspect ratio, centers the image, and fills every remaining target cell in full-grid mode.

The Calendar API supports a fixed event palette rather than arbitrary RGB. Full-grid therefore uses a Calendar `colorId` for its canvas:

```powershell
--calendar-background-color-id 8
```

When omitted, the deterministic project default is `8`. Foreground RGB still passes through nearest-color and contrast mapping; the selected structural background does not. Calendar may render the same color differently in light and dark themes, so the frame plan is theme-independent and the final appearance requires visual comparison.

## Dry-run commands

Sparse:

```powershell
python -m calendar_anim calendar map-frame .\output\primeiro-teste\animation.json --frame 0 --mapping-mode sparse --start-date 2026-09-07 --run-id primeiro-frame-sparse-01
```

Full-grid:

```powershell
python -m calendar_anim calendar map-frame .\output\primeiro-teste\animation.json --frame 0 --mapping-mode full-grid --calendar-background-color-id 8 --start-date 2026-09-07 --run-id primeiro-frame-full-grid-01
```

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
- `mapping-report.txt` includes both mode estimates;
- `frame-plan.json` records mode, background, roles, metrics, events, and metadata.

The report distinguishes expanded source cells, fitted foreground, structural background, total cells, foreground/background events, foreground colors, and execution limits.

For the tested frame 0:

```text
Source grid: 28x20
Target grid: 42x24
Expanded source cells: 75
Foreground cells after fitting: 101

Sparse events: 101
Full-grid background cells: 907
Full-grid events: 1008
```

## Cost of fidelity

Full-grid intentionally performs no filler optimization:

```text
42x24 = 1008 events/frame
1008 x 12 frames = 12096 events
1008 x 60 frames = 60480 events
```

These totals are planning estimates, not a claim that Google Calendar will safely accept those workloads. The baseline prioritizes visual fidelity over event economy.

## Real upload safety

The normal single-frame execution limit is 1200 events and the absolute configurable ceiling remains 2000. Dry-run is never blocked by the normal limit; real execution is blocked before authentication when the plan exceeds it.

Real execution additionally requires:

- explicit `--execute`;
- explicit `--start-date`;
- a complete calibration profile;
- confirmation defaulting to `N`, unless `--yes` is supplied;
- the recognized secondary `Calendar Animation Lab`;
- no existing frame with the same run metadata.

The confirmation discloses mapping mode, target grid, foreground events, background events, total events, calendar, frame, and run ID.

```powershell
python -m calendar_anim calendar map-frame .\output\primeiro-teste\animation.json --frame 0 --mapping-mode full-grid --calendar-background-color-id 8 --start-date 2026-09-07 --run-id primeiro-frame-full-grid-01 --execute
```

Partial failures preserve planned, created, failed, foreground-created, background-created, IDs, and errors in `execution-result.json`.

Cleanup removes foreground and background together because both share the same run metadata:

```powershell
python -m calendar_anim calendar cleanup --animation-id primeiro-teste --run-id primeiro-frame-full-grid-01
python -m calendar_anim calendar cleanup --animation-id primeiro-teste --run-id primeiro-frame-full-grid-01 --execute
```

## Why there is no Playwright yet

Calendar API writes are not visually atomic. The future animation will pre-upload frames into separate weeks, wait for stabilization, capture each week, and compose the screenshots. This branch does not implement multi-frame upload, batching, retry/resume, Playwright, or MP4/GIF composition.

## Roadmap

```text
full-grid single frame
    -> compare with real Calendar
    -> validate subcolumn ordering
    -> validate shape fidelity
    -> test 2-3 frames
    -> identify fillers that can be removed safely
    -> future hybrid mapping
    -> multi-frame planner
    -> upload/retry/resume
    -> Playwright capture
    -> final MP4/GIF
```

Sparse remains a valid future optimization. A hybrid strategy may eventually retain fillers only where they are structurally required, but it is deliberately outside this baseline.
