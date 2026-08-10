# High-detail 126x72 animation validation

This workflow is an explicit validation candidate. It does not change the production 42x24
default. It reuses the production zero-width ordering, synchronized horizontal-band compression,
checkpointed uploader, resumable capture, and GIF composer.

## Render and local plan

```powershell
.\.venv\Scripts\python.exe -m calendar_anim render .\color-input.mp4 --start 0 --duration 2 --frames 6 --width 126 --height 72 --palette calendar --colors 6 --background "#000000" --background-tolerance 35 --output-fps 3 --output .\output\high-detail-validation\color-126x72
.\.venv\Scripts\python.exe -m calendar_anim calendar plan-animation .\output\high-detail-validation\color-126x72\animation.json --start-date 2027-06-06 --run-id high-detail-animation-126x72-01 --frame-count 6 --experimental-grid 126x72 --max-events 1200
.\.venv\Scripts\python.exe -m calendar_anim calendar upload-animation --run-id high-detail-animation-126x72-01 --resume
```

The persisted animation plan must record `126x72`, 18 slots per day, 15-minute rows,
`06:00-00:00`, `zero-width`, and `synchronized-horizontal-bands`. The six selected weeks begin on
2027-06-06 and are a local planning choice; the planning command does not query Calendar.

## Real upload gate

Inspect the dry-run totals and ensure every frame is at or below the unchanged 1,200-event limit.
Only then run the explicit upload command:

```powershell
.\.venv\Scripts\python.exe -m calendar_anim calendar upload-animation --run-id high-detail-animation-126x72-01 --resume --execute
```

The uploader checkpoints each frame. A retry skips completed frames; a partial frame requires the
existing `--recover-partial` flow.

## Capture and compose after upload

After all six frames are `completed` with planned events equal to created events:

```powershell
.\.venv\Scripts\python.exe -m calendar_anim calendar capture-animation --run-id high-detail-animation-126x72-01 --capture-profile high-detail-126x72 --execute
.\.venv\Scripts\python.exe -m calendar_anim calendar compose-capture --run-id high-detail-animation-126x72-01 --fps 3
```

The high-detail capture profile persists native Chrome zoom 33%, dark week view, a visible week
header, and the exact `06:00-00:00` window. It captures the fixed header and time window separately
and joins them without hiding Calendar UI. Capture state remains resumable.

Manual visual acceptance remains required for the final Calendar GIF. A successful local render or
dry-run does not establish visual equivalence in Google Calendar.
