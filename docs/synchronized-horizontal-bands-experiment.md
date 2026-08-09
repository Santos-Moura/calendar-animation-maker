# Synchronized horizontal-band compression experiment

Independent vertical compression was rejected because mixed durations and partial overlaps changed
Google Calendar's horizontal order, visible widths, and placement. This follow-up uses a stricter
rule: every compressed interval contains all six subcolumns with exactly the same start and end.

The production mapper is still unchanged. This branch only estimates savings and generates a
small Calendar calibration.

## Algorithm

For each day and logical row, construct the complete six-slot vector:

```text
[slot 00, slot 01, slot 02, slot 03, slot 04, slot 05]
```

Each slot contributes its Calendar `color_id` and structural `cell_role`. Consecutive rows merge
only when the entire vector is identical. A change in one slot ends the whole band.

Example:

```text
06:00 [BG, BG, GREEN, GREEN, BG, BG]
06:30 [BG, BG, GREEN, GREEN, BG, BG]  -> same band
07:00 [BG, BG, GREEN, BG,    BG, BG]  -> new band
```

Each resulting band costs exactly six events. All six use technical summaries `00..05` and share
the same start and end, avoiding the mixed-duration overlap that failed previously. Bands never
cross day boundaries.

## Local estimate

```powershell
python -m calendar_anim calendar estimate-band-compression .\output\multi-frame-test\animation.json
```

Measured on the current six-frame manifest:

| Frame | Baseline | Bands | Compressed events | Reduction |
| ---: | ---: | ---: | ---: | ---: |
| 0 | 1008 | 22 | 132 | 86.9% |
| 1 | 1008 | 22 | 132 | 86.9% |
| 2 | 1008 | 23 | 138 | 86.3% |
| 3 | 1008 | 22 | 132 | 86.9% |
| 4 | 1008 | 22 | 132 | 86.9% |
| 5 | 1008 | 21 | 126 | 87.5% |
| **Total** | **6048** | **132** | **792** | **86.9%** |

The estimate writes JSON and text below:

```text
output/compression-estimates/<animation_id>/synchronized-bands/
```

It is entirely local and never creates compressed `CalendarEventDraft` objects.

## Visual calibration

The `synchronized-horizontal-bands` pattern creates five adjacent bands between 06:00 and 18:00.
Every band contains six equal-duration events. Green represents foreground and graphite represents
structural background; the color vector changes between bands.

Dry-run:

```powershell
python -m calendar_anim calendar calibrate --pattern synchronized-horizontal-bands --start-date 2026-11-22 --run-id synchronized-bands-real-01
Invoke-Item .\output\calibration\synchronized-bands-real-01\expected-layout.png
Invoke-Item .\output\calibration\synchronized-bands-real-01\calibration-report.txt
```

Real execution, only after reviewing the artifacts:

```powershell
python -m calendar_anim calendar calibrate --pattern synchronized-horizontal-bands --start-date 2026-11-22 --run-id synchronized-bands-real-01 --execute
```

Observe whether:

- all six widths remain equal in every band;
- summaries remain ordered `00..05`;
- green and graphite appear in the expected slots;
- boundaries between adjacent bands stay aligned;
- refresh and week navigation preserve the result.

Edit the generated `calibration-observations.yaml`, replacing only observed `null` values, then
record it:

```powershell
python -m calendar_anim calendar record-calibration --run-id synchronized-bands-real-01 --pattern synchronized-horizontal-bands --observations-file .\output\calibration\synchronized-bands-real-01\calibration-observations.yaml
python -m calendar_anim calendar calibration-summary
```

Preview cleanup before real deletion:

```powershell
python -m calendar_anim calendar cleanup --animation-id calibration-synchronized-horizontal-bands --run-id synchronized-bands-real-01
python -m calendar_anim calendar cleanup --animation-id calibration-synchronized-horizontal-bands --run-id synchronized-bands-real-01 --execute
```

## Decision gate

Do not implement production compression unless equal widths, summary order, color placement,
adjacent boundaries, and refresh/navigation stability all pass. A positive result would justify a
new feature branch from `main`; this experimental branch should not be merged as production logic.

