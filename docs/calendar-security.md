# Calendar integration security

Safety is enforced at the command and application boundaries:

- dry-run is the default and performs no Google API call for calibration or cleanup;
- every write or deletion requires `--execute`;
- `--yes` skips confirmation only, never validation, event limits, or metadata filters;
- the default event limit is 30 and the absolute calibration limit is 100;
- the primary calendar is explicitly rejected;
- the lab calendar has a fixed project description and its ID is cached in `.calendar-anim/calendar-config.json`;
- credentials and OAuth tokens are separate from that ordinary config and ignored by Git;
- duplicate `animation_id` plus `run_id` runs are rejected before event creation;
- cleanup requires `generated_by=calendar-anim`, an explicit `animation_id`, and an explicit `run_id`;
- cleanup deletes only event IDs returned by that filtered query; there is no `--all` operation.

OAuth requests `calendar.app.created` and read-only CalendarList access. Local login is manual in Google's browser page; the program never receives a password. Protect `credentials.json` and `token.json` as secrets. They, `.env` files, `.calendar-anim/`, Playwright profiles, and authentication state are ignored.

Preview cleanup from local execution records without authentication:

```bash
calendar-anim calendar cleanup \
  --animation-id calibration-overlap-columns --run-id <run_id>
```

Without `--execute`, cleanup reports locally recorded information and does not authenticate. With
`--execute`, it performs an authenticated metadata lookup, shows the exact match count, asks for
confirmation, and deletes those matches only.

To rotate local authentication, delete `token.json` and authenticate again. To revoke server-side access, revoke the app in Google Account security settings. Never use the main calendar for calibration and never manually repoint the local config to `primary`.
