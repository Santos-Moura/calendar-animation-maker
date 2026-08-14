# Archived: Final hybrid Calendar capture

This workflow validates the completed Account-B recurrence upload visually before
capturing or composing the final animation. Every browser operation is read-only;
the commands contain no Calendar API write, update, delete, or cleanup path.

## Locked source and boundary

- `input.mp4`, locked by a local SHA-256 fingerprint
- clip 114.0-150.0 seconds, 108 frames, 3 FPS, 126x72
- `cayde-final` palette, zero-width summaries, synchronized horizontal bands
- indices 0-22 (human frames 1-23): `account-a`, Chrome zoom 33%
- indices 23-107 (human frames 24-108): `account-b`, Chrome zoom 90%
- both profiles use the required Calendar vertical scroller for 06:00-00:00

The planner requires Account-A indices 0-22 to be complete and the Account-B
recurrence state to be exactly 32,021/32,021 complete. It deliberately ignores
the partial Account-A index 23. No frame is split between profiles.

## 1. Account-B sanity capture

First run only the six representative Account-B weeks:

```powershell
.\.venv\Scripts\python.exe -m calendar_anim calendar capture-hybrid-sanity --run-id cayde-final-hybrid-rdate-126x72-3fps-36s-01 --profile account-b --frames 24,40,60,80,100,108 --execute
```

Each frame produces a raw browser viewport, logical event-grid crop, normalized
504x288 capture, expected local frame, and metrics. The report checks expected
occurrence population, rendered DOM population, color presence, gross logical
alignment, and geometry. The contact sheet places expected and Calendar results
side-by-side. Visual approval is still mandatory even if the automated result is
`PASS`.

Artifacts are under:

```text
output/hybrid-runs/cayde-final-hybrid-rdate-126x72-3fps-36s-01/sanity/
```

If any frame is clearly empty, shifted, wrongly colored, or otherwise wrong,
stop. Do not proceed to seam or full capture.

## 2. A/B seam geometry

After visually approving sanity, capture only human frame 23 from A and human
frame 24 from B:

```powershell
.\.venv\Scripts\python.exe -m calendar_anim calendar capture-hybrid-seam --run-id cayde-final-hybrid-rdate-126x72-3fps-36s-01 --execute
```

Both logical crops are normalized independently to 504x288. The report compares
logical cell geometry with a 5% gross mismatch gate and produces
`seam/a-b-transition-geometry.png`. This compares crop and geometry, not the two
different animation images.

## 3. Full capture

Only after sanity and seam report `PASS`:

```powershell
.\.venv\Scripts\python.exe -m calendar_anim calendar capture-final-hybrid --run-id cayde-final-hybrid-rdate-126x72-3fps-36s-01 --execute
```

The command is resumable per frame and automatically switches from Account A to
Account B at index 23. It writes exactly:

```text
final-frames/frame_000.png
...
final-frames/frame_107.png
```

All outputs are 504x288, normalized from the same logical 126x72 event-grid
bounds with nearest-neighbor resizing. No blur, sharpening, color correction, or
aspect-ratio distortion is applied.

## 4. Silent MP4

After all 108 PNG files exist:

```powershell
.\.venv\Scripts\python.exe -m calendar_anim calendar compose-final-hybrid --run-id cayde-final-hybrid-rdate-126x72-3fps-36s-01
```

The source is the PNG sequence—not a GIF. Encoding is 3 FPS, 504x288, H.264 High,
CRF 10, preset slow, yuv420p, SAR 1:1. The command rejects gaps, duplicates,
wrong filenames, wrong dimensions, or a duration outside 36 seconds.

## 5. Source-audio mux

Finally:

```powershell
.\.venv\Scripts\python.exe -m calendar_anim calendar mux-final-hybrid-audio --run-id cayde-final-hybrid-rdate-126x72-3fps-36s-01 --source-video input.mp4
```

The source hash is revalidated. Audio is extracted directly from 114.0 to 150.0
seconds; AAC is stream-copied when possible. The silent video and source audio
are muxed to `final/final-with-audio.mp4`, and measured durations must agree
within 50 ms.

## Cleanup policy

No command in this workflow performs cleanup. Account A events, Account B
recurring parents, and validation resources remain untouched until the final
video is visually approved.
