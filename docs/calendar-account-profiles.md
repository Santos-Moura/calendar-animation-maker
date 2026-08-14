# Google Calendar account profiles

Profiles isolate OAuth tokens, selected secondary calendars, capture zoom, and persistent Chrome
data. They are ownership boundaries, not a way to evade Calendar quotas.

Local profile data lives below the ignored `.calendar-anim/` directory. A profile record may
contain account and calendar identifiers, but OAuth token contents remain in separate ignored JSON
files. Nothing under this directory belongs in Git.

```bash
python -m calendar_anim calendar profiles list
python -m calendar_anim calendar profiles auth --profile account-b --execute
python -m calendar_anim calendar profiles inspect --profile account-b --remote
python -m calendar_anim calendar profiles create-calendar \
  --profile account-b --name "Calendar Animation Lab B" --execute
```

Authentication and calendar creation require explicit execution and confirmation. The profile
service rejects `primary`, shared calendars not owned by the selected account, identity changes,
and calendar metadata that does not match the saved profile.

API OAuth and browser login are intentionally separate. Authenticate the capture profile manually:

```bash
python -m calendar_anim calendar browser-login --profile account-b
```

Close Chrome before starting capture so Playwright can lock the persistent profile directory.
