# Archived: Cayde final 216-frame candidate

The approved final variant is now isolated as
`cayde-final-216f-6fps-cyan-magenta-rdate-126x72-36s-01`. It uses Cyan Magenta,
with Calendar background `colorId 7`, foreground colorIds `3,5,9,11`, and weeks
`2030-05-05` through `2034-06-18`. The earlier candidate namespace remains preview/source
material and must not be uploaded.

The 216-frame final run is isolated from the completed 108-frame run. Its locked identity is
`cayde-final-216f-6fps-cyan-magenta-rdate-126x72-36s-01`; all local plans live below
`output/216-plans/<run-id>/`, and future runtime capture/media artifacts live below
`output/216-runs/<run-id>/`.

Prepare local frame mappings, recurrence/RDATE compaction, expansion gates, payload sizing, and
ETA without opening Google Calendar:

```powershell
.\.venv\Scripts\python.exe -m calendar_anim calendar prepare-cayde-216 `
  --run-id cayde-final-216f-6fps-cyan-magenta-rdate-126x72-36s-01 `
  --input .\input.mp4
```

The source render must contain 216 genuine samples from `input.mp4` over `114.0-150.0s` at 6
FPS. The mapper uses `126x72`, `cayde-cyan-magenta`, synchronized horizontal bands, zero-width
ordering, Account B, and 216 consecutive weeks beginning `2030-05-05`.

The remote preflight is explicit and read-only:

```powershell
.\.venv\Scripts\python.exe -m calendar_anim calendar preflight-cayde-216 `
  --run-id cayde-final-216f-6fps-cyan-magenta-rdate-126x72-36s-01 `
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

This search was the selection gate. After Cyan Magenta and the first clean window were approved,
the final plan was regenerated under its new run ID; the old candidate plan remains non-final.

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

After the final local plan and exact remote preflight both pass, the only approved bulk command
is:

```powershell
.\.venv\Scripts\python.exe -m calendar_anim calendar upload-cayde-216-recurrence `
  --run-id cayde-final-216f-6fps-cyan-magenta-rdate-126x72-36s-01 `
  --profile account-b `
  --input .\input.mp4 `
  --resume `
  --execute
```

It revalidates hashes, palette, dates, exact recurrence expansion, final read-only preflight,
Calendar identity, and an empty write-time window before its interactive confirmation. It uses
atomic checkpoints, deterministic parent reconciliation, adaptive short rate-limit handling,
and the established long-lived quota cooldown policy.
