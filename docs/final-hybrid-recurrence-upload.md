# Final hybrid recurrence upload

The final Account-B uploader consumes the already-approved artifacts for
`cayde-final-hybrid-rdate-126x72-3fps-36s-01`. It does not regenerate the video,
palette, frame mapping, ordering, or recurrence plan.

## Locked boundary

- Human frames 1-23 (indices 0-22) remain as existing standalone events in
  `account-a`. The uploader cannot write to or clean that profile.
- Human frames 24-108 (indices 23-107) are complete Account-B frames represented
  by 214,596 logical occurrences in 32,021 recurring parents.
- The partial Account-A frame 24 remains untouched and is not used for the final
  capture. Frame 24 is represented in full by Account B.
- RDATE chunk size stays at 100. Parent IDs and the 32,021-parent sequence come
  from the hashed, immutable local plan.

## Local gate

Run the local preparation whenever the artifacts or checkpoint need validation:

```powershell
.\.venv\Scripts\python.exe -m calendar_anim calendar prepare-hybrid-recurrence-upload --run-id cayde-final-hybrid-rdate-126x72-3fps-36s-01
```

This command performs no authentication and no Calendar API call. It checks the
source SHA-256, all locked configuration fields, recurrence expansion equality,
unique deterministic IDs, RDATE syntax/timezones, summary/color serialization,
chunk sizes, and payload limits. It writes only local reports under:

```text
output/hybrid-runs/cayde-final-hybrid-rdate-126x72-3fps-36s-01/
```

The dry-run must report 214,596 logical occurrences, 32,021 parents, zero
duplicates/missing/extra occurrences, and expansion equality `YES`.

## Real upload

The real command is intentionally restricted to `account-b` and requires both
`--resume` and `--execute`:

```powershell
.\.venv\Scripts\python.exe -m calendar_anim calendar upload-hybrid-recurrence --run-id cayde-final-hybrid-rdate-126x72-3fps-36s-01 --profile account-b --resume --execute
```

Before the single confirmation prompt, the command authenticates Account B,
verifies the calendar name/ID, owner access, and timezone, then performs a
read-only scan of the final weeks on a fresh checkpoint. An unrelated event in
those weeks aborts the upload. Known validation resources are not deleted or
mixed into this run.

After confirmation the process is unattended:

- a compact state file is atomically replaced at checkpoints;
- completed parents are skipped on resume;
- uploading/partial parents are looked up by deterministic ID;
- a matching `409 Conflict` is reconciled as success;
- a lost response is reconciled before another insert;
- short rate limits use adaptive pacing plus bounded exponential backoff;
- `quotaExceeded` checkpoints immediately, then waits 15m, 30m, 60m, 2h,
  4h, 4h... with jitter;
- quota probes use the same next missing parent, never a synthetic event;
- the maximum continuous automatic quota wait is 48 hours;
- temporary network failures use bounded retries and stop with a safe partial
  checkpoint if exhausted;
- one Ctrl+C preserves the checkpoint; the same command resumes after restart.

The initial/floor write interval is 1.0 second. A quota recovery restarts at a
conservative 1.5 seconds. Rate-limit pacing state and quota-wait timestamps are
persisted across restarts.

Performance is written as JSON and text beside the state. Logical RDATE
occurrences are reported separately from `events.insert` parent calls.

## Cleanup (future, explicit only)

Dry-run description:

```powershell
.\.venv\Scripts\python.exe -m calendar_anim calendar cleanup-hybrid-recurrence --run-id cayde-final-hybrid-rdate-126x72-3fps-36s-01 --profile account-b
```

Adding `--execute` authenticates Account B, requires owner access to exactly
`Calendar Animation Lab B`, finds parents using all three private metadata keys
(`generated_by`, exact `run_id`, and `calendar_profile=account-b`), prints the
match count, and asks for confirmation. It can never target Account A. Cleanup is
not run automatically by preparation, upload, validation, or capture.

## Later capture and composition

Capture remains a separate future step: frames 1-23 use browser profile A at 33%
zoom; frames 24-108 use browser profile B at 90% zoom. Both are normalized from
the 126x72 logical crop to 504x288 with the approved 06:00-00:00 window. Final
video composition continues from PNG screenshots at 3 FPS (H.264 High, CRF 10,
slow, yuv420p, SAR 1:1); GIF is not a video source.
