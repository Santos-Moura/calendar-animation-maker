# Calendar visual calibration

Google Calendar controls event height, overlap columns, colors, and text visibility. The local grid cannot be mapped honestly until those behaviors are measured. Test one pattern at a time in week view with a stable zoom and viewport.

## Patterns

- `duration-scale` — 7 isolated events of 5, 10, 15, 20, 30, 45, and 60 minutes. It measures both the minimum visible event and the minimum height that is actually distinguishable.
- `overlap-columns` — exactly 21 events in separate groups of 1 through 6 simultaneous events. Every group lasts 45 minutes and has deterministic titles, metadata, and colors.
- `color-palette` — 11 separately timed event color IDs with approximate hex references. Browser rendering may differ.
- `position-grid` — 6 events on Monday, Wednesday, and Friday, morning and afternoon.
- `horizontal-bars` — 15 experimental events; proportional width is not assumed.
- `combined` — a smoke overview, not a replacement for individual patterns.

## Dry-run first

The following command does not authenticate and does not call Google:

```powershell
python -m calendar_anim calendar calibrate --pattern overlap-columns --start-date 2026-08-10 --run-id overlap-preview
```

Inspect these files in `output/calibration/overlap-preview/`:

- `calibration-plan.json` — exact event data that would be sent;
- `calibration-report.txt` — groups, target UI conditions, and a manual worksheet;
- `expected-layout.png` — a logical side-by-side expectation only;
- `execution-result.json` — records that this was a dry-run.

The PNG is deliberately labelled as a logical expectation. It does not reproduce or promise Google Calendar's real overlap algorithm.

## Recommended UI conditions

Use one stable setup for both calibration patterns:

- week view;
- `America/Sao_Paulo` timezone;
- browser zoom at 100%;
- target viewport of 1920×1080;
- sidebar hidden;
- weekends visible;
- visible window from 06:00 to 18:00.

If the real viewport differs, record the actual width and height. Do not silently treat the target as a measured result.

## Real overlap calibration

After OAuth setup, choose a future Monday and run:

```powershell
python -m calendar_anim calendar calibrate --pattern overlap-columns --start-date 2026-08-10 --run-id overlap-real-01 --execute
```

The CLI displays the plan and asks for confirmation before it authenticates and writes. It creates or reuses only the secondary `Calendar Animation Lab`, and rejects a duplicate `run_id`.

In Google Calendar, inspect the six non-overlapping time groups:

- `overlap-1`: 1 event at 09:00–09:45;
- `overlap-2`: 2 simultaneous events at 10:00–10:45;
- `overlap-3`: 3 simultaneous events at 11:00–11:45;
- `overlap-4`: 4 simultaneous events at 12:00–12:45;
- `overlap-5`: 5 simultaneous events at 13:00–13:45;
- `overlap-6`: 6 simultaneous events at 14:00–14:45.

For every group, answer the same checklist:

1. Are the events visually separated?
2. Do they all have similar width?
3. Does any event partially overlap another?
4. Does the visual order appear predictable?
5. Is the title shown?
6. Is the color distinguishable?
7. Is the block still usable as a visual “pixel”?

Base `usable_overlap_columns` primarily on visual separation, not title readability. Text is useful for calibration but is not important to the final animation. “Maximum tested” is 6 for this pattern. “Usable” is your conservative measured limit and may be lower.

This experiment measures how many visual subcolumns are useful. It does **not** prove that `block.width` equals a number of simultaneous events, that widths are uniform, or that creation order determines visual order. Wider logical blocks remain a separate `horizontal-bars` experiment.

## Recording measurements

The old `--minimum-event-minutes` option remains accepted, but new records should distinguish visibility from useful height.

Record the vertical result from a `duration-scale` run:

```powershell
python -m calendar_anim calendar record-calibration --run-id duration-real-01 --pattern duration-scale --minimum-visible-event-minutes 5 --minimum-distinguishable-height-minutes 30 --browser-zoom 100 --viewport-width 1920 --viewport-height 1080 --visible-start-hour 6 --visible-end-hour 18 --sidebar-hidden --weekends-visible
```

After inspecting the overlap run, replace `5` below with the conservative number of columns you found usable:

```powershell
python -m calendar_anim calendar record-calibration --run-id overlap-real-01 --pattern overlap-columns --maximum-tested-overlap-columns 6 --usable-overlap-columns 5 --browser-zoom 100 --viewport-width 1920 --viewport-height 1080 --titles-visible --colors-distinguishable --notes "Five columns remained reliably readable."
```

Each invocation writes the run-specific `calibration-observations.yaml` and updates `output/calibration/calibration-profile.yaml` without erasing measurements from the other axis. Unknown values stay null or pending; they are never invented.

## Consolidated summary

```powershell
python -m calendar_anim calendar calibration-summary
```

This command reads local YAML only and never calls the Calendar API. With a 06:00–18:00 window and 30 distinguishable minutes, it derives 24 logical rows. With 7 visible days and 5 usable overlap columns, it derives 35 logical columns. If either measurement is absent, the corresponding value is shown as `pending`.

To overlay a particular run-specific observation on the consolidated profile:

```powershell
python -m calendar_anim calendar calibration-summary --run-id overlap-real-01
```

## Safe cleanup

Preview the exact metadata match first, then explicitly execute deletion:

```powershell
python -m calendar_anim calendar cleanup --animation-id calibration-overlap-columns --run-id overlap-real-01
python -m calendar_anim calendar cleanup --animation-id calibration-overlap-columns --run-id overlap-real-01 --execute
```

Cleanup targets only events with matching `generated_by`, `animation_id`, and `run_id` in the recognized lab calendar.

## Current boundary

This phase validates only the static geometry assumptions. It does not implement the real video-to-calendar mapper, bulk upload, browser automation, recording, or final animation composition. The next implementation step is one real static video frame after the vertical and horizontal measurements are repeatable.
