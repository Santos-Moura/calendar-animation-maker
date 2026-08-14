# Supported workflow

The reference run is a 216-frame, 6 FPS, 36-second animation on a 126x72 logical grid. Commands
default to the locked run ID, `account-b`, `input.mp4`, `header_preserved_fill`, and `1512x864`.
Override Make variables only when the underlying CLI explicitly permits the value.

## 1. Local plan

```bash
make plan-216
```

Direct CLI:

```bash
python -m calendar_anim calendar prepare-cayde-216 \
  --run-id cayde-final-216f-6fps-cyan-magenta-rdate-126x72-36s-01 \
  --input input.mp4
```

This samples the source, maps the Cyan Magenta palette, builds deterministic event occurrences,
compacts them into recurrence parents, and writes ignored local artifacts. Google access: none.

## 2. Remote preflight

```bash
make preflight-216
```

Direct CLI:

```bash
python -m calendar_anim calendar preflight-cayde-216 \
  --run-id cayde-final-216f-6fps-cyan-magenta-rdate-126x72-36s-01 \
  --profile account-b --execute
```

Here `--execute` enables a read-only API request. The command verifies authenticated ownership,
the selected secondary calendar, timezone, protected artifact hashes, deterministic IDs, and an
empty target range. Calendar writes: none.

## 3. Resumable upload

```bash
make upload-216
```

Direct CLI:

```bash
python -m calendar_anim calendar upload-cayde-216-recurrence \
  --run-id cayde-final-216f-6fps-cyan-magenta-rdate-126x72-36s-01 \
  --profile account-b --input input.mp4 --resume --execute
```

This is a real write operation. It asks for confirmation, records every completed parent
atomically, reconciles deterministic IDs on resume, adapts to temporary rate limits, and safely
pauses on long-lived Calendar usage quotas.

## 4. Read-only capture

Authenticate the persistent browser profile manually once, then run:

```bash
make capture-216
```

Direct CLI:

```bash
python -m calendar_anim calendar capture-final-cayde-216 \
  --run-id cayde-final-216f-6fps-cyan-magenta-rdate-126x72-36s-01 \
  --profile account-b --frames 1-216 --mode header_preserved_fill \
  --resolution 1512x864 --resume --execute
```

Capture opens Calendar read-only. Completed PNGs are checkpointed per frame; a failed frame is
retried without deleting successful captures or changing Calendar events.

## 5. Local media

```bash
make recompose-216
make compose-216
make mux-216
```

These steps add the approved Calendar toolbar from protected capture components, compose the 216
PNGs at 6 FPS, and mux the exact 114-150 second source-audio clip. They do not open a browser or
call Google APIs. Reports record codecs, dimensions, durations, and A/V synchronization.

## Account profiles

Profiles keep OAuth tokens, selected calendars, and persistent browser data under the ignored
`.calendar-anim/` directory:

```bash
python -m calendar_anim calendar profiles list
python -m calendar_anim calendar profiles auth --profile account-b --execute
python -m calendar_anim calendar profiles inspect --profile account-b --remote
```

Profile setup never authorizes a primary calendar. See
[calendar-account-profiles.md](calendar-account-profiles.md).

## Recovery

Rerun the same upload or capture command with `--resume`. Do not delete partial work manually.
Upload state uses deterministic event IDs and remote reconciliation; capture state skips completed
PNGs. Quota pauses are not permanent failures.
