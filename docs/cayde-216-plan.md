# Cayde final 216-frame candidate

The 216-frame candidate is isolated from the completed 108-frame run. Its locked identity is
`cayde-final-216f-6fps-rdate-126x72-36s-01`; all local plans live below
`output/216-plans/<run-id>/`, and future runtime capture/media artifacts live below
`output/216-runs/<run-id>/`.

Prepare local frame mappings, recurrence/RDATE compaction, expansion gates, payload sizing, and
ETA without opening Google Calendar:

```powershell
.\.venv\Scripts\python.exe -m calendar_anim calendar prepare-cayde-216 `
  --run-id cayde-final-216f-6fps-rdate-126x72-36s-01 `
  --input .\input.mp4
```

The source render must contain 216 genuine samples from `input.mp4` over `114.0-150.0s` at 6
FPS. The mapper remains `126x72`, `cayde-final`, synchronized horizontal bands, zero-width
ordering, Account B, and 216 consecutive weeks beginning seven days after the old final week.

The remote preflight is explicit and read-only:

```powershell
.\.venv\Scripts\python.exe -m calendar_anim calendar preflight-cayde-216 `
  --run-id cayde-final-216f-6fps-rdate-126x72-36s-01 `
  --profile account-b `
  --execute
```

It checks Account B identity, Calendar owner/timezone, protected old-artifact hashes, and the
entire proposed half-open date range. Any unexpected event produces `STOP`; the command never
inserts, updates, deletes, or cleans up Calendar resources.

Before selecting a final palette, generate the three isolated candidates locally:

```powershell
.\.venv\Scripts\python.exe -m calendar_anim calendar preview-cayde-216-palettes `
  --run-id cayde-final-216f-6fps-rdate-126x72-36s-01
```

The command generates eight representative frames, one GIF and one contact sheet per
candidate, plus a three-way comparison. Candidate presets remap the dominant source canvas
`#7986CB` to their explicit structural background, but do not change `cayde-final` and are not
selected automatically.

Search Account B for two distinct clean windows with one read-only expanded-event query:

```powershell
.\.venv\Scripts\python.exe -m calendar_anim calendar search-cayde-216-windows `
  --run-id cayde-final-216f-6fps-rdate-126x72-36s-01 `
  --profile account-b `
  --execute
```

The current local recurrence plan remains blocked after this search. It must be regenerated
only after the user approves a palette and a clean window; no bulk upload command is exposed by
this preparation flow.

Future capture must use Account B at 90%, `header_preserved_fill`, `1512x864`, the left time
gutter, header, and `06:00-00:00`. In addition to week/view/structural/stability readiness, a
frame expecting occurrences must contain approved Calendar-palette visual occupancy. A
practically empty screenshot is retried up to three times and is never checkpointed completed.

Future media commands are prepared but must not run before upload and capture are approved:

```powershell
.\.venv\Scripts\python.exe -m calendar_anim calendar compose-final-cayde-216
.\.venv\Scripts\python.exe -m calendar_anim calendar mux-final-cayde-216-audio
```

Composition requires exactly `frame_000.png` through `frame_215.png` at 6 FPS. Audio mux uses
the exact `114.0-150.0s` source interval and copies the H.264 video stream.
