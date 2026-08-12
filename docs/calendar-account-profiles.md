# Google Calendar account profiles

Calendar profiles isolate OAuth tokens, selected secondary calendars, and persistent Chrome data.
They are account/calendar ownership boundaries, not a mechanism for distributing requests to evade
Google quotas.

## Storage and compatibility

The legacy configuration remains profile `account-a`:

```text
account-a
  OAuth client: credentials.json (or GOOGLE_CALENDAR_CREDENTIALS_FILE)
  token: token.json (or GOOGLE_CALENDAR_TOKEN_FILE)
  calendar config: .calendar-anim/calendar-config.json
  browser: .calendar-anim/browser-profile
```

The secondary profile is isolated:

```text
.calendar-anim/profiles/account-b/
  profile.json
  token.json

.calendar-anim/browser-profiles/account-b/
```

Capture zoom is also profile-scoped. `account-a` keeps its approved high-detail value of 33%,
while `account-b` uses native Chrome zoom 90%. Recurrence capture still uses the existing Calendar
vertical-scroller discovery and positioning, scrolls after opening each week, and validates that
the exact 06:00–00:00 window fits before taking the screenshot. There is deliberately no
no-scroll fallback.

The same Desktop OAuth client can authorize multiple Google users. Tokens remain separate. Profile
JSON stores path references, account identity, calendar identity/name/timezone, and no OAuth token
contents. `.calendar-anim/`, `credentials*.json`, `token*.json`, and `client_secret*.json` are
ignored by Git.

The scopes remain deliberately limited:

- `calendar.app.created` creates secondary calendars and manages events in calendars created by
  this application;
- `calendar.calendarlist.readonly` identifies the account and finds the marked lab calendar.

No password is read or entered in the terminal.

## Profile commands

List local state without a Google request:

```powershell
.\.venv\Scripts\python.exe -m calendar_anim calendar profiles list
```

Authentication is a dry-run unless `--execute` is supplied. The real command prints the isolated
token target and asks for confirmation before opening Google's OAuth page:

```powershell
.\.venv\Scripts\python.exe -m calendar_anim calendar profiles auth --profile account-b --calendar-name "Calendar Animation Lab B" --execute
```

After OAuth, verify the saved identity. `--remote` performs only account/calendar reads and refuses
a token whose primary account differs from the identity recorded for the profile:

```powershell
.\.venv\Scripts\python.exe -m calendar_anim calendar profiles inspect --profile account-b --remote
```

Select an existing app-marked secondary calendar with the expected name, or explicitly create it.
The command shows profile, authenticated account, calendar name, and ID before confirmation and
never selects `primary` or a shared calendar not owned by the selected account:

```powershell
.\.venv\Scripts\python.exe -m calendar_anim calendar profiles create-calendar --profile account-b --name "Calendar Animation Lab B" --execute
```

## Account-B recurrence gate

The prepared validation `recurrence-rdate-account-b-01` targets six new weeks from 2030-02-03
through 2030-03-10. It contains one parent with three displayed instances (`DTSTART + 2 RDATE`) and
three standalone controls. All resources copy the real `U+200B U+200B` summary, `colorId=1`,
Sunday 06:00–08:15 geometry, and `America/Sao_Paulo` timezone.

```powershell
.\.venv\Scripts\python.exe -m calendar_anim calendar upload-recurrence-validation --validation-id recurrence-rdate-account-b-01 --profile account-b --execute
```

Execution resolves only account B's selected calendar, verifies plan/profile/calendar agreement,
prints the wrong-account fail-safe summary, checks all six weeks for unrelated events, and then
asks for confirmation. A clean first run makes exactly four `events.insert` calls. A quota or rate
limit error stops immediately and records the result; it does not enter the unattended bulk wait.

Cleanup requires all three private filters and queries only account B's selected calendar:

```text
generated_by=calendar-anim-recurrence-validation
validation_id=recurrence-rdate-account-b-01
calendar_profile=account-b
```

## Browser capture and sharing

API OAuth and the persistent Chrome session are intentionally separate. For a dedicated B browser
session, log in manually once, close Chrome, and then capture:

```powershell
.\.venv\Scripts\python.exe -m calendar_anim calendar browser-login --profile account-b
.\.venv\Scripts\python.exe -m calendar_anim calendar capture-recurrence-validation --validation-id recurrence-rdate-account-b-01 --profile account-b --execute
```

Alternatively, share `Calendar Animation Lab B` manually from Google Calendar settings with the
Google user already used by the existing Playwright profile. Grant only the visibility needed for
capture, ensure Calendar B is enabled in the Calendar UI, and override the Chrome directory:

```powershell
.\.venv\Scripts\python.exe -m calendar_anim calendar capture-recurrence-validation --validation-id recurrence-rdate-account-b-01 --profile account-b --profile-directory .calendar-anim/browser-profile --execute
```

Sharing is not automated and no ACL scope is requested. Capture writes no Calendar data. Its
side-by-side sheet supports human comparison of color, invisible summary, duration, position,
width, borders, and rendering; it does not claim pixel equivalence automatically.

## Future hybrid model

`MultiFramePlan`, `FrameUploadPlan`, `AnimationUploadState`, `CaptureFramePlan`, recurrence
occurrences, and recurrence parents can now encode `calendar_profile` and, where known,
`calendar_id`. Old artifacts default to `account-a`. Recurrence grouping includes its destination
profile/calendar boundary, so visually equal occurrences on A and B cannot be combined into the
same parent.

This representation allows a future reviewed migration to preserve A's completed and partial
single events while assigning only missing occurrences to B recurrence parents. It does not create
that migration, upload the estimated ~31,850 parents, or merge screenshots from multiple visible
calendars yet.

## Cleanup

After visual review, optional cleanup is:

```powershell
.\.venv\Scripts\python.exe -m calendar_anim calendar cleanup-recurrence-validation --validation-id recurrence-rdate-account-b-01 --profile account-b --execute
```

It displays the B identity and calendar ID before its final confirmation. Account A is never
queried by this command.
