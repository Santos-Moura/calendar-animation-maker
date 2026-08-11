# Final cutscene run preparation

The final candidate uses the source clip `114.0 -> 150.0` seconds at `126x72`. Every new
render records the source SHA-256 in `animation.json`, so plans cannot silently reuse the
identity of an older `input.mp4`.

## Locked artistic palette

Use `--palette-preset cayde-final` when planning Calendar frames. The preset deliberately
locks the approved artistic mapping:

- structural background: Calendar `colorId=1` (`#7986CB`);
- foreground: only `colorId=1,2,3,4`;
- foreground colors use deterministic nearest-color mapping without a background-dependent
  contrast fallback.

This is an artistic palette, not an attempt to reproduce the source RGB values exactly.

## Upload recovery

Animation uploads remain serial. Every event draft receives a deterministic Google-compatible
ID derived from its immutable contents. A lost response can therefore be retried without
creating a duplicate; Google `409 Conflict` confirms that the intended event already exists.

Retryable failures are limited to `429`, `5xx`, timeouts, and recognized temporary transport
errors. The default policy is five event attempts with exponential backoff, jitter, and a
30-second ceiling. A frame gets at most three automatic recovery cycles per invocation.
Permanent errors do not loop.

On resume, completed frames are skipped. Partial, interrupted, and failed frames are reconciled
against their deterministic event IDs and only missing drafts are submitted. Unknown legacy
event IDs trigger metadata-scoped cleanup of that frame as a safe fallback. Persistent failure
is checkpointed as `failed` and stops the run before later frames.

## Final media composition

After every Calendar week has been captured, compose directly from the PNG screenshots:

```powershell
.\.venv\Scripts\python.exe -m calendar_anim calendar compose-final `
  --run-id <run-id> `
  --source-video .\input.mp4 `
  --clip-start 114 `
  --clip-end 150 `
  --fps 2
```

The command requires both `ffmpeg` and `ffprobe`. It writes under
`output/animation-runs/<run-id>/final/`:

- `preview.gif`;
- `calendar-animation.mp4` encoded directly from PNG captures;
- `cutscene-audio.m4a` extracted from the exact source interval;
- `final-with-audio.mp4`;
- `composition-report.json` with measured visual, audio, and mux durations.

The command rejects a frame count/FPS duration that differs from the requested audio interval.
It does not contact Google Calendar.

### Pixel-art MP4 quality

MP4 video is encoded directly from the PNG sequence, never from the GIF. The compositor uses
integer dimensions, nearest-neighbor scaling, square pixels (`SAR 1:1`), H.264 High profile,
`yuv420p`, `preset=slow`, and `CRF=10`. The compatibility-oriented `yuv420p` conversion is not
mathematically lossless, but the low CRF substantially reduces edge/color error while remaining
widely playable.

GIF timing is stored in centiseconds. At 3 FPS, the requested 333.333 ms frame interval becomes
330 ms, so 108 GIF frames report approximately 35.64 seconds. MP4 retains the exact 3 FPS and
36.00-second timeline and is authoritative for audio sync.

At native `504x288` size, the logical `126x72` preview is an exact 4x integer enlargement. A
player may still blur either file when its window is resized to a non-integer multiple. Inspect
at 100%/native size, or use a player configured for nearest-neighbor video scaling, when judging
pixel edges.

## Safety gate

Do not raise `--max-events` automatically. Measure the real frames, report maximum, p95, and
the count above the existing guard, then obtain explicit approval for any guard change.

The approved final run is the sole exception: run ID
`cayde-final-126x72-3fps-36s-01` may persist a per-frame ceiling of `5200`. The ordinary
high-detail ceiling remains `2500`, and even the final run is rejected above `5200`. The
exception is checked both while planning and immediately before upload.
