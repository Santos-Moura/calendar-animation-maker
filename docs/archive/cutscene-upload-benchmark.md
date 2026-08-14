# Archived: 126x72 cutscene upload benchmark

This experiment measures the existing serial uploader without changing its batching, retry,
rate-limit, checkpoint, or resume behavior. Its fixed source is `input.mp4`, clipped from 115 to
118 seconds and rendered as nine 126x72 frames at 3 FPS.

The run uses the explicit high-detail geometry: 18 slots per day, 15-minute rows, and the
06:00-00:00 visible window. Full-grid mapping uses zero-width summaries and synchronized
horizontal bands. The normal production defaults remain unchanged; this run explicitly opts into
the high-detail ceiling of 2,500 events per frame.

## Local plan

```powershell
.\.venv\Scripts\python.exe -m calendar_anim calendar plan-animation .\output\cutscene-1m55s-1m58s-126x72\animation.json --start-date 2027-08-01 --run-id cutscene-1m55s-1m58s-126x72-01 --frame-start 0 --frame-count 9 --experimental-grid 126x72 --mapping-mode full-grid --event-compression synchronized-horizontal-bands --subcolumn-ordering zero-width --calendar-background-color-id 8 --max-events 2500
```

Planning is local. The selected weeks, 2027-08-01 through 2027-09-26, were checked separately with
a read-only Calendar query before the plan was created.

Inspect the pending actions without authentication or API calls:

```powershell
.\.venv\Scripts\python.exe -m calendar_anim calendar upload-animation --run-id cutscene-1m55s-1m58s-126x72-01 --resume
```

## Real benchmark gate

The real command retains interactive confirmation and must only be run after manual approval:

```powershell
.\.venv\Scripts\python.exe -m calendar_anim calendar upload-animation --run-id cutscene-1m55s-1m58s-126x72-01 --resume --execute
```

`upload-performance.json` and `upload-performance.txt` live in the run directory. Frame durations
use `time.perf_counter()`, while human-readable start and finish timestamps use UTC datetimes. A
resume invocation records already-completed frames separately and does not upload or count them
again. Partial recovery remains the existing delete-and-recreate flow and preserves earlier
invocation history in the performance report.

No capture or composition belongs to this gate. Capture starts only after all nine frame states are
completed with no partial or failed frames.
