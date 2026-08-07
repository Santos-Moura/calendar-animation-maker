# Calendar visual calibration

Google Calendar controls event height, overlap columns, colors, and text visibility. The local grid cannot be mapped honestly until those behaviors are measured. Test one pattern at a time in week view with a stable zoom and viewport.

## Patterns

- `duration-scale` — 7 isolated events of 5, 10, 15, 20, 30, 45, and 60 minutes. Observe minimum visible height and title visibility.
- `overlap-columns` — 21 events in separate groups of 1 through 6 simultaneous events. This is the primary test for column order and usable subcolumns.
- `color-palette` — 11 separately timed event color IDs with approximate hex references. The browser rendering may differ from the approximation.
- `position-grid` — 6 events on Monday, Wednesday, and Friday, morning and afternoon. Check week start, timezone, day placement, and vertical offset.
- `horizontal-bars` — 15 events in groups representing 1 through 5 logical units. This is experimental; proportional width is not assumed.
- `combined` — 27 selected duration, overlap, color, and position events. It is a smoke overview, not a replacement for individual patterns.

## Dry-run first

```bash
calendar-anim calendar calibration-patterns
calendar-anim calendar calibrate \
  --pattern overlap-columns --start-date 2026-08-10
```

Inspect `calibration-plan.json`, `calibration-report.txt`, and `expected-layout.png`. The PNG visualizes the logical expectation with a deterministic 1400×900 canvas; Google's real overlap algorithm may differ.

The default is 30 events. Raising it requires `--max-events`; no calibration may exceed the absolute limit of 100. `--yes` without `--execute` does not perform API calls.

## Real calibration

After completing OAuth setup:

```bash
calendar-anim calendar calibrate \
  --pattern duration-scale --start-date 2026-08-10 --execute
calendar-anim calendar calibrate \
  --pattern overlap-columns --start-date 2026-08-10 --execute
```

The command shows the plan before authentication/writing, asks for confirmation, creates or reuses `Calendar Animation Lab`, and rejects a duplicate `run_id`. Open Google Calendar manually, select week view, show only the lab calendar if practical, and compare it to the logical preview.

Record measurements:

```bash
calendar-anim calendar record-calibration \
  --run-id <run_id> --pattern overlap-columns \
  --minimum-event-minutes 15 --usable-overlap-columns 5 \
  --browser-zoom 80 --viewport-width 1920 --viewport-height 1080
```

The resulting `calibration-observations.yaml` depends on human observation. Unknown values remain null; they must not be treated as measured defaults.

## Manual worksheet

```text
Run ID:
Pattern:
Browser:
Viewport:
Zoom:
Calendar view:
Sidebar:
Weekends:
Minimum visible duration:
Usable overlap columns:
Color observations:
Unexpected behavior:
Notes:
```

Once measurements are repeatable, derive a calibration profile and adapt the manifest mapper. Then upload only one real video frame, not the full animation.
