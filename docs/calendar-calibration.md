# Calendar visual calibration

Google Calendar controls event height, overlap, colors, borders, padding, and text visibility. The mapper must therefore use measurements from the real Calendar UI instead of assuming that a local pixel grid maps directly to events.

The current `42x24` grid is a candidate derived from completed vertical and overlap measurements. It is not final, and `block.width` is never sent as one Calendar event: mapper strategies expand blocks into logical cells first.

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
python -m calendar_anim calendar record-calibration --run-id position-real-20260807-01 --pattern position-grid --browser-zoom 100 --viewport-width 1920 --viewport-height 1080 --visible-start-hour 6 --visible-end-hour 18 --week-alignment-ok --timezone-alignment-ok --day-alignment-ok --vertical-alignment-ok --week-starts-on sunday --notes "Observed Sunday-to-Saturday week with correct days, times, and timezone."
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

### Recorded result

At 100% browser zoom and 1920x1080, bars of 1-6 cells remained recognizable in both light and dark themes. Cells appeared contiguous but retained thin theme-colored separators, so the recorded result is:

```text
independent cells contiguous: yes
visible gaps/separators: yes
same-color cells merge completely: no
maximum useful width: 6
recommended strategy: independent-cells
```

This is acceptable pixel-art segmentation. It does not prove that sparse events can address arbitrary subcolumns. The full-grid mapper therefore creates six simultaneous cells for every day/row and uses background events as structural fillers.

## `subcolumn-order`

Six overlapping events create six visual columns, but Google Calendar decides their final left-to-right layout. Full-grid guarantees six simultaneous structural cells; visual fidelity also requires stable subcolumn ordering.

The pattern creates 24 events in four groups:

```text
09:00 forward-1  S0 S1 S2 S3 S4 S5
10:00 forward-2  S0 S1 S2 S3 S4 S5
11:00 reverse    S5 S4 S3 S2 S1 S0
12:00 shuffled   S2 S5 S0 S4 S1 S3
```

Each slot keeps one distinctive color. `creation_sequence` records the list position, while `subcolumn_index` records the logical slot represented by that event. The plan, serialization, calibration service, fake gateway, and Google gateway all preserve the supplied list order. This is still a hypothesis about the UI, not proof of visual ordering.

Dry-run:

```powershell
python -m calendar_anim calendar calibrate --pattern subcolumn-order --start-date 2026-09-07 --run-id slot-order-real-01
```

Open `expected-layout.png` and `calibration-report.txt`. The image is the expected logical creation order, not a guarantee of Google Calendar visual ordering.

Real calibration, only after reviewing the dry-run:

```powershell
python -m calendar_anim calendar calibrate --pattern subcolumn-order --start-date 2026-09-07 --run-id slot-order-real-01 --execute
```

Observe all four groups, refresh the browser, navigate away and back, and reopen Calendar if practical. Then record only what was actually observed:

```powershell
python -m calendar_anim calendar record-calibration --run-id slot-order-real-01 --pattern subcolumn-order --browser-zoom 100 --viewport-width 1920 --viewport-height 1080 --visual-order-forward "0,1,2,3,4,5" --visual-order-reverse "5,4,3,2,1,0" --visual-order-shuffled "2,5,0,4,1,3" --stable-after-refresh --stable-after-navigation --stable-after-reopen --creation-order-controls-layout --recommended-slot-order-strategy creation-order --notes "Replace with real observations."
```

Do not copy those observation flags unless the real Calendar result supports them. Negative paired flags are available, and omitted values remain `null`/pending.

## Consolidated profile and readiness

```powershell
python -m calendar_anim calendar calibration-summary
```

The profile schema keeps older YAML compatible while adding optional `color_mapping`, `position_mapping`, `horizontal_bar_mapping`, `subcolumn_order_mapping`, and derived `candidate_grid` sections.

`Mapper readiness` is diagnostic only:

- `NOT READY`: at least one required experiment has no recorded measurements;
- `READY FOR SINGLE-FRAME EXPERIMENT`: vertical, overlap, colors, position, horizontal bars, and a stable creation-order subcolumn strategy all have recorded measurements.

Readiness does not block local mapping and does not mark `42x24` as final. It does block a real single-frame upload. Position readiness includes `week_starts_on`; horizontal-bar readiness includes continuity, gaps, same-color merging, maximum useful width, and the recommended strategy. Subcolumn-order readiness requires stable results after refresh, navigation, and reopen, plus evidence that creation order controls layout. A recorded negative result remains valuable evidence but does not unlock upload.

## Cleanup

Preview each deletion before using `--execute`:

```powershell
python -m calendar_anim calendar cleanup --animation-id calibration-color-palette --run-id color-real-20260807-01
python -m calendar_anim calendar cleanup --animation-id calibration-position-grid --run-id position-real-20260807-01
python -m calendar_anim calendar cleanup --animation-id calibration-horizontal-bars --run-id bars-real-20260807-01
python -m calendar_anim calendar cleanup --animation-id calibration-subcolumn-order --run-id slot-order-real-01
```

Cleanup matches only `generated_by`, `animation_id`, and `run_id` in the recognized lab calendar.

## Mapper baseline

The **Single Frame Calendar Mapper** supports `sparse` and `full-grid`. Sparse is efficient but horizontally unstable; full-grid is the recommended first visual baseline because every calibrated slot exists. Dry-run can be used before readiness, while real upload waits for a complete profile. See [single-frame mapper](single-frame-mapper.md).

The next manual experiment is `subcolumn-order`. Only after it is recorded as stable should a full-grid plan be reviewed for an explicitly confirmed single-frame upload. Multiple frames, hybrid optimization, Playwright, browser capture, batching, retry, and resume remain outside this phase.
