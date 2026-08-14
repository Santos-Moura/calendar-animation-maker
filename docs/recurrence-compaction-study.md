# Recurrence/RDATE compaction study

This experiment is local-only. It does not modify the immutable animation plan, upload state,
or any Google Calendar event. Run it with:

```powershell
.\.venv\Scripts\python.exe -m calendar_anim calendar recurrence-plan `
  --run-id cayde-final-126x72-3fps-36s-01 `
  --parent-chunk-size 100
```

Artifacts are written under
`output/recurrence-studies/cayde-final-126x72-3fps-36s-01/`.

## Signature and ordering

Recurrence grouping runs after `synchronized-horizontal-bands`. The visual signature includes
the plan timezone, weekday, local start time, duration, exact summary, `colorId`, transparency,
visibility, and event type. It excludes absolute week/date, frame/run bookkeeping, local
`color_hex`, logical coordinates, band metadata, and the original deterministic event ID.

The exact invisible zero-width summary remains on the recurring parent. Google documents the
start/end fields as the first instance and the recurrence field as the repeated schedule; an
instance-specific exception is required to give one instance a different summary. The expected
behavior is therefore inheritance of the parent summary and `colorId`, but horizontal ordering
and pixel equivalence in week view remain **REQUIRES SMALL REAL VALIDATION**.

Identical occurrences at the same absolute start cannot share a parent because RFC 5545 treats
duplicate recurrence instances as one. The planner partitions that multiplicity into separate
recurrence lanes before RDATE chunking.

## RDATE representation

The parent uses the first occurrence as `start`/`end`. Remaining starts are emitted as a local
DATE-TIME RDATE with an IANA timezone, for example:

```text
RDATE;TZID=America/Sao_Paulo:20270112T080000,20270202T080000
```

RFC 5545 applies the same exact `DTSTART`/`DTEND` duration to generated members of the recurrence
set. Google exposes recurring instances through `recurringEventId` and `originalStartTime`.

## Limits

- Google Calendar Help documents a maximum of 730 occurrences per recurring event.
- An official RDATE-count limit is **UNKNOWN**.
- An official `recurrence[]` byte/line limit is **UNKNOWN**.
- An official `events.insert` request-body size limit is **UNKNOWN**.
- Whether generated recurring instances count individually toward the long-lived general
  Calendar usage limit is **UNKNOWN**. The insert-call reduction is exact; usage-limit relief
  must be demonstrated by the small real validation before migrating the remaining animation.
- The prototype is therefore chunked and reports results for 25, 50, 100, and 250 total
  occurrences per parent. The migration plan defaults to 100.

## Metadata, cleanup, and resume

Each parent has a deterministic Google-compatible ID derived from the source run, exact visual
signature, recurrence lane, chunk index, and covered occurrence keys. Google private metadata
contains only `run_id`, `recurrence_group_id`, `signature_hash`, and `chunk_index`. The larger
parent-to-frame and parent-to-occurrence mapping remains in `recurrence-plan.json`.

A future uploader can reconcile a parent by deterministic ID, treat `409 Conflict` as an
idempotent success, clean up by the run metadata, and expand the local mapping when capture or
diagnostics need an instance-to-frame relationship.

The hybrid migration leaves completed frames and all locally known individual event IDs intact.
Only missing occurrences are placed into recurrence parents. Remote reconciliation is required
immediately before any real migration upload.

## Batch requests

Batching is not a usage-limit solution. Google documents that a batch is unpacked into its inner
requests and `n` inner calls count as `n` usage-limit requests. It can reduce connection overhead
only.

## Sources

- Google Calendar API, recurring event concepts:
  <https://developers.google.com/workspace/calendar/api/concepts/events-calendars>
- Google Calendar API, recurring events guide:
  <https://developers.google.com/workspace/calendar/api/guides/recurringevents>
- Google Calendar API, Event resource:
  <https://developers.google.com/workspace/calendar/api/v3/reference/events>
- Google Calendar API, batch requests:
  <https://developers.google.com/workspace/calendar/api/guides/batch>
- Google Calendar Help, 730-occurrence limit:
  <https://support.google.com/calendar/answer/37115?hl=pt-BR>
- RFC 5545:
  <https://www.rfc-editor.org/rfc/rfc5545>
