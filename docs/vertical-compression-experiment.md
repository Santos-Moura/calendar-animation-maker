# Vertical event compression experiment

The working full-grid baseline creates one event for every logical cell:

```text
42 columns x 24 rows = 1008 events per frame
```

This experiment asks whether consecutive vertical cells with the same Calendar color and the
same structural role can become one longer event. It is vertical run-length encoding applied to
the already mapped `42x24` canvas.

The experiment does **not** change the production mapper, uploader, capture, GIF composition, or
default behavior. It measures potential savings locally and generates a small real-Calendar
calibration for manual inspection.

## Why visual calibration is required

Google Calendar owns the overlap layout. Six events with equal starts and ends may remain in six
stable subcolumns, while mixed durations and partial overlaps may change widths or horizontal
positions. A mathematically correct run therefore is not automatically a visually safe Calendar
event.

Two independent conditions are required before a production feature should be considered:

1. the real Calendar layout must remain visually acceptable;
2. the measured event reduction must be large enough to justify the extra complexity.

## Local estimator

Run the estimator against an existing manifest:

```powershell
python -m calendar_anim calendar estimate-compression .\output\multi-frame-test\animation.json
```

It loads the existing calibration profile, builds the same full-grid mapped canvas locally, and
groups consecutive cells per logical `x` when both `color_id` and `cell_role` match. No compressed
events are generated and no OAuth or Calendar API call occurs.

The default artifacts are:

```text
output/compression-estimates/<animation_id>/
|-- vertical-compression-estimate.json
`-- vertical-compression-estimate.txt
```

Metrics are reported per frame and in total:

- baseline events;
- compressed runs;
- saved events and reduction percentage;
- foreground and background runs;
- longest vertical run;
- average run length.

## Calibration groups

The `vertical-compression` pattern creates exactly 30 events, all with color ID `2` and technical
summary keys `00..05`:

| Group | Time | Events | Purpose |
| --- | --- | ---: | --- |
| CONTROL | 06:00-08:00 | 12 | Three slots, each represented by four 30-minute cells |
| COMPRESSED | 08:30-10:30 | 6 | Six slots, each represented by one 120-minute event |
| MIXED LENGTH | 11:00-13:00 | 6 | Same start; durations 30, 60, 90, 120, 90, and 60 minutes |
| STAGGERED | 14:00-16:30 | 6 | Different starts and ends with partial overlaps |

The control is intentionally compact to remain within the existing 30-event safety limit. Its
first three slots can be compared directly with the corresponding compressed slots.

## Manual workflow

Create only local artifacts first:

```powershell
python -m calendar_anim calendar calibrate --pattern vertical-compression --start-date 2026-11-15 --run-id vertical-compression-real-01
Invoke-Item .\output\calibration\vertical-compression-real-01\expected-layout.png
Invoke-Item .\output\calibration\vertical-compression-real-01\calibration-report.txt
```

Review the logical expectation, then explicitly create real events only if desired:

```powershell
python -m calendar_anim calendar calibrate --pattern vertical-compression --start-date 2026-11-15 --run-id vertical-compression-real-01 --execute
```

Inspect the real Calendar in the standard week-view conditions. Edit the generated file without
guessing any result:

```text
output/calibration/vertical-compression-real-01/calibration-observations.yaml
```

Replace the relevant `null` values with `true` or `false`, add notes, then validate and record it:

```powershell
python -m calendar_anim calendar record-calibration --run-id vertical-compression-real-01 --pattern vertical-compression --observations-file .\output\calibration\vertical-compression-real-01\calibration-observations.yaml
python -m calendar_anim calendar calibration-summary
```

The experimental conclusion appears in the summary but does not participate in current mapper
readiness.

Preview cleanup before deleting anything:

```powershell
python -m calendar_anim calendar cleanup --animation-id calibration-vertical-compression --run-id vertical-compression-real-01
python -m calendar_anim calendar cleanup --animation-id calibration-vertical-compression --run-id vertical-compression-real-01 --execute
```

## Decision

If the Calendar observation is positive and the estimate is relevant, create a new
`feature/vertical-event-compression` branch from `main`. Keep the baseline mode available during
that future implementation.

If the visual experiment fails, record that vertical compression is not reliable and consider
API upload improvements, controlled concurrency, hybrid mapping, or background optimization. Do
not use DOM or CSS hacks to force Calendar layout.

