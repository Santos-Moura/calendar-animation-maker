# Recurrence smallest real validation

This experiment compares three rendered instances from one recurring parent against three
standalone controls without changing the existing animation run. The source visual signature is
copied from event 0 of frame 23 in `cayde-final-126x72-3fps-36s-01`:

- summary: `U+200B U+200B` (the real zero-width ordering key)
- `colorId`: `1`
- local interval: Sunday 06:00–08:15
- timezone: `America/Sao_Paulo`
- effective defaults: opaque, default visibility, default event type

The recurring set has three dates: its `DTSTART` on 2029-12-02 plus two explicit `RDATE` values on
2029-12-16 and 2029-12-30. Google Calendar recurrence always includes `DTSTART`; putting three
additional `RDATE` values in the parent would create four recurring instances and require seven
weeks once the three controls are included. This six-week gate therefore uses `DTSTART + 2 RDATE`
and describes that distinction explicitly in every artifact.

## Local preparation

Planning is local and makes no authentication or Google request:

```powershell
.\.venv\Scripts\python.exe -m calendar_anim calendar prepare-recurrence-validation --validation-id recurrence-rdate-smallest-real-01 --source-run-id cayde-final-126x72-3fps-36s-01 --source-frame-index 23 --source-event-index 0 --start-week 2029-12-02
```

The immutable plan and report are written under
`output/recurrence-validation/recurrence-rdate-smallest-real-01/`.

## Explicit real upload

The command remains a dry-run unless `--execute` is present. With `--execute`, it authenticates,
resolves the already-existing laboratory calendar, reads the six-week window, and aborts before
the first insert if any unrelated event is present. It never creates a new calendar.

```powershell
.\.venv\Scripts\python.exe -m calendar_anim calendar upload-recurrence-validation --validation-id recurrence-rdate-smallest-real-01 --execute
```

Interactive confirmation is mandatory. A fresh run issues four `events.insert` calls: one
recurring parent and three standalone controls. Deterministic resource IDs and private validation
metadata make a later invocation reconcile existing resources and insert only missing ones.
`upload-report.json` records actual insert calls plus separate `rateLimitExceeded` and
`quotaExceeded` counts. A rejected insert stops the validation and atomically preserves partial
progress; it does not clean up or continue submitting writes.

## Capture and comparison

After upload status is `completed`, capture all six weeks using the existing persistent Playwright
profile, native Chrome zoom 33%, and the 06:00–00:00 visible window:

```powershell
.\.venv\Scripts\python.exe -m calendar_anim calendar capture-recurrence-validation --validation-id recurrence-rdate-smallest-real-01 --execute
```

The command saves six screenshots, SHA-256 hashes, a capture report, and
`captures/recurring-vs-standalone.png`, with one recurring/standalone pair per row. Capture performs
no Calendar API write.

## Scoped cleanup

Cleanup queries recurring masters without expanding instances and requires both private filters:

```text
generated_by=calendar-anim-recurrence-validation
validation_id=recurrence-rdate-smallest-real-01
```

It then deletes only the returned parent/control resource IDs. It does not query by the final
animation's `run_id` and cannot select its existing events.

```powershell
.\.venv\Scripts\python.exe -m calendar_anim calendar cleanup-recurrence-validation --validation-id recurrence-rdate-smallest-real-01 --execute
```

Interactive confirmation is mandatory. Without `--execute`, upload, capture, and cleanup are all
local dry-runs.

For the isolated secondary-account variant, see
[Google Calendar account profiles](calendar-account-profiles.md). The B plan uses validation ID
`recurrence-rdate-account-b-01`, new weeks, and profile-scoped private metadata.
