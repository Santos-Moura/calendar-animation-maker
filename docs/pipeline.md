# Processing pipeline

1. Inspect the video and validate clip bounds.
2. Sample source frames uniformly without loading the complete video.
3. Crop and fit each frame using `contain`, `cover`, or `stretch`.
4. Quantize deterministically to the selected Calendar palette.
5. Convert pixels to logical cells and synchronized horizontal bands.
6. Assign one frame to one Calendar week.
7. Compact equivalent events across weeks into recurrence parents with bounded RDATE chunks.
8. Preflight the target account, calendar, date range, and immutable plan hashes.
9. Upload with deterministic IDs, atomic checkpoints, resume, and quota-aware pacing.
10. Capture each week through a persistent browser profile and stable structural grid bounds.
11. Recompose the approved Calendar UI regions, encode H.264, and mux the source audio.

Local previews and manifests allow visual approval before any remote write. See
[workflow.md](workflow.md) for the supported commands.
