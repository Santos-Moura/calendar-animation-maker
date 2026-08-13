# Account-B final prefix

The final visual capture uses one persistent browser profile: `account-b` at 90% Chrome zoom.
The already uploaded Account-B recurrence bulk remains immutable:

- human frames 24-108, weeks 2028-03-19 through 2029-10-28;
- 32,021 recurring parents and 214,596 logical occurrences;
- no parent update, extension, rechunk, deletion, or recreation.

The complementary prefix is a separate recurrence namespace:

- run `cayde-final-b-prefix-rdate-frames-001-023-01`;
- human frames 1-23, weeks 2027-10-10 through 2028-03-12;
- `segment=prefix`, `human_frames=1-23`, and `calendar_profile=account-b` metadata;
- RDATE chunk size 100 and a 1.0-second minimum write interval.

## Local preparation

```powershell
.\.venv\Scripts\python.exe -m calendar_anim calendar prepare-account-b-prefix-recurrence
.\.venv\Scripts\python.exe -m calendar_anim calendar prepare-account-b-prefix-upload
```

Both commands are local-only. They do not authenticate or call the Calendar API.

## Future upload

Only after reviewing the generated sizing report:

```powershell
.\.venv\Scripts\python.exe -m calendar_anim calendar upload-account-b-prefix-recurrence --run-id cayde-final-b-prefix-rdate-frames-001-023-01 --profile account-b --resume --execute
```

The real command checks owner access, timezone, Calendar identity, the clean prefix window, and
the locked local expansion before asking for interactive confirmation. Checkpoint/reconciliation,
rate-limit pacing, long-lived quota waits, and Ctrl+C safety are inherited from the validated
recurrence uploader.

## Future read-only audit

```powershell
.\.venv\Scripts\python.exe -m calendar_anim calendar audit-account-b-prefix-recurrence-remote --run-id cayde-final-b-prefix-rdate-frames-001-023-01 --profile account-b --frames 1,8,15,23
```

This command uses only `events.list` and `events.get` and requires exact expansion for all four
representative frames.

## Future single-profile capture

After the prefix upload is complete and its audit is exact:

```powershell
.\.venv\Scripts\python.exe -m calendar_anim calendar capture-final-single-profile --run-id cayde-final-hybrid-rdate-126x72-3fps-36s-01 --profile account-b --frames 1-108 --mode header_preserved_fill --resolution 1512x864 --execute
```

This flow writes capture artifacts to a separate single-profile directory and never opens the
Account-A browser profile. Calendar capture remains read-only.
