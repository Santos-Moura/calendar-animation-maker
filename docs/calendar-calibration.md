# Calendar visual calibration

Google Calendar controls event height, overlap, colors, borders, padding, and text visibility. The mapper must therefore use measurements from the real Calendar UI instead of assuming that a local pixel grid maps directly to events.

The current `42x24` grid is a candidate derived from completed vertical and overlap measurements. It is not final, and `block.width` must not be interpreted as a number of simultaneous events until the horizontal-bar experiment is reviewed.

## Standard UI conditions

Keep the following conditions stable during every experiment:

- view: Week;
- timezone: `America/Sao_Paulo`;
- browser zoom: 100%;
- target viewport: 1920x1080;
- sidebar: hidden;
- weekends: visible;
- visible range: approximately 06:00-18:00;
- only `Calendar Animation Lab` visible when practical.

Record the actual conditions if they differ. The program never observes browser geometry automatically.

## Safety and artifacts

`calendar calibrate` is always a dry-run unless `--execute` is supplied. Real execution asks for confirmation, uses the secondary lab calendar, enforces a default limit of 30 events and an absolute limit of 100, and rejects duplicate `run_id` values.

Every dry-run writes:

```text
output/calibration/<run_id>/
|-- calibration-plan.json
|-- calibration-report.txt
|-- expected-layout.png
`-- execution-result.json
```

`expected-layout.png` is a deterministic logical reference. It is not a simulation of Google Calendar's rendering algorithm.

After manual inspection, `record-calibration` additionally writes `calibration-observations.yaml` and updates `output/calibration/calibration-profile.yaml`.

## Completed measurements

### `duration-scale`

The measured minimum visible event is 5 minutes and the minimum clearly distinguishable height is 30 minutes. A 06:00-18:00 window therefore yields the current candidate of 24 logical rows.

### `overlap-columns`

Groups of 1-6 simultaneous events remained visually separated. With seven visible days, six usable subcolumns per day yield the current candidate of 42 logical columns.

This proves only that six subcolumns are distinguishable in the controlled setup. It does not prove that a wider logical block can be encoded directly as simultaneous events.

## `color-palette`

### Purpose

Compare the 11 event color IDs supported by the current Calendar abstraction. Every event is isolated, has the same duration, and carries its `color_id`, an internal logical name, and an approximate hexadecimal reference.

The hexadecimal value is not claimed to be the exact browser-rendered color.

### Commands

```powershell
python -m calendar_anim calendar calibrate --pattern color-palette --start-date 2026-08-17 --run-id color-real-20260807-01
python -m calendar_anim calendar calibrate --pattern color-palette --start-date 2026-08-17 --run-id color-real-20260807-01 --execute
```

### Manual checklist

- Are all colors distinguishable?
- Which colors have the best contrast?
- Which pairs appear too similar?
- Which colors look muted or should be avoided?
- How many colors should the mapper use?
- Which `colorId` values form the candidate palette?

### Recording

The values below are examples of syntax only. Replace them with observed IDs:

```powershell
python -m calendar_anim calendar record-calibration --run-id color-real-20260807-01 --pattern color-palette --browser-zoom 100 --viewport-width 1920 --viewport-height 1080 --preferred-color-ids "1,5,7,9" --recommended-color-count 4 --poor-contrast-color-ids "8" --similar-color-groups "1,9;2,10" --notes "Replace with real observations."
```

Use an empty string for a measured empty list, for example `--poor-contrast-color-ids ""`. Omit an option when it has not been measured.

## `position-grid`

### Purpose

Validate week, day, timezone, and vertical placement using nine known positions:

```text
Monday    06:00, 12:00, 17:30
Wednesday 06:00, 12:00, 17:30
Friday    06:00, 12:00, 17:30
```

The 17:30 events end at 18:00 and test the lower edge of the candidate visible range.

### Commands

```powershell
python -m calendar_anim calendar calibrate --pattern position-grid --start-date 2026-08-24 --run-id position-real-20260807-01
python -m calendar_anim calendar calibrate --pattern position-grid --start-date 2026-08-24 --run-id position-real-20260807-01 --execute
```

### Manual checklist

- Does the week start on the expected day?
- Are Monday, Wednesday, and Friday in the expected columns?
- Is the timezone correct?
- Is there an unexpected one-hour offset?
- Do 06:00, 12:00, and 17:30 appear at the expected heights?
- Is the 06:00-18:00 range still stable?

### Recording

Use the positive or negative form of every measured flag. The following example represents a fully aligned observation and must not be copied if the UI differs:

```powershell
python -m calendar_anim calendar record-calibration --run-id position-real-20260807-01 --pattern position-grid --browser-zoom 100 --viewport-width 1920 --viewport-height 1080 --visible-start-hour 6 --visible-end-hour 18 --week-alignment-ok --timezone-alignment-ok --day-alignment-ok --vertical-alignment-ok --week-starts-on monday --notes "Replace with real observations."
```

Negative forms include `--week-alignment-not-ok`, `--timezone-alignment-not-ok`, `--day-alignment-not-ok`, and `--vertical-alignment-not-ok`.

## `horizontal-bars`

### Purpose

Test whether 1-6 simultaneous, same-color events look like one continuous horizontal bar. Each unit is an independent event with strategy metadata `independent-cells`.

Unlike `overlap-columns`, cells in one bar share a color so borders, gaps, padding, and rounded corners remain visible during inspection.

This pattern deliberately does not attempt partial internal positioning. Google chooses simultaneous-event placement, and the current code has no honest mechanism for selecting an internal subcolumn. That result remains unknown rather than being forced through complex logic.

### Commands

```powershell
python -m calendar_anim calendar calibrate --pattern horizontal-bars --start-date 2026-08-31 --run-id bars-real-20260807-01
python -m calendar_anim calendar calibrate --pattern horizontal-bars --start-date 2026-08-31 --run-id bars-real-20260807-01 --execute
```

### Manual checklist

- Do same-color cells appear to form one continuous bar?
- Are gaps visible?
- Do rounded borders prevent visual merging?
- Do widths 2-6 appear proportional?
- Is placement predictable?
- Should every logical block be decomposed into unit cells?

### Recording

The following example represents a positive `independent-cells` result. Use the negative flag when the real UI differs:

```powershell
python -m calendar_anim calendar record-calibration --run-id bars-real-20260807-01 --pattern horizontal-bars --browser-zoom 100 --viewport-width 1920 --viewport-height 1080 --independent-cells-contiguous --no-visible-cell-gaps --same-color-cells-merge --maximum-useful-bar-width 6 --recommended-horizontal-strategy independent-cells --notes "Replace with real observations."
```

Available opposite flags include `--independent-cells-not-contiguous`, `--visible-cell-gaps`, and `--same-color-cells-do-not-merge`.

## Consolidated profile and readiness

```powershell
python -m calendar_anim calendar calibration-summary
```

The profile schema keeps old vertical and overlap YAML compatible while adding optional `color_mapping`, `position_mapping`, `horizontal_bar_mapping`, and derived `candidate_grid` sections.

`Mapper readiness` is diagnostic only:

- `NOT READY`: at least one required experiment has no recorded measurements;
- `READY FOR SINGLE-FRAME EXPERIMENT`: vertical, overlap, colors, position, and horizontal bars all have recorded measurements.

Readiness does not block other commands and does not mark `42x24` as final.

## Cleanup

Preview each deletion before using `--execute`:

```powershell
python -m calendar_anim calendar cleanup --animation-id calibration-color-palette --run-id color-real-20260807-01
python -m calendar_anim calendar cleanup --animation-id calibration-position-grid --run-id position-real-20260807-01
python -m calendar_anim calendar cleanup --animation-id calibration-horizontal-bars --run-id bars-real-20260807-01
```

Cleanup matches only `generated_by`, `animation_id`, and `run_id` in the recognized lab calendar.

## Next delivery

After all five experiments are recorded, the next delivery is **Single Frame Calendar Mapper**. It will read one manifest frame, use the calibration profile, create a dry-run event plan, report its event count, and optionally upload only that frame for visual comparison.

Multiple frames, full animation, Playwright, browser capture, batching, retry, and resume remain outside this phase.
