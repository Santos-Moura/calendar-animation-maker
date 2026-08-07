# Google Calendar OAuth setup

The real calibration command uses Google's installed-app OAuth flow. It never accepts or stores a Google password.

1. Open Google Cloud Console and create or select a project.
2. Enable **Google Calendar API** for that project.
3. Configure the OAuth consent screen. For a personal test app, keep it in testing and add your Google account as a test user when required.
4. Create an OAuth client with application type **Desktop app**.
5. Download the client JSON.
6. Save it as `credentials.json` in the project root, or point to it with `GOOGLE_CALENDAR_CREDENTIALS_FILE`.
7. Optionally set `GOOGLE_CALENDAR_TOKEN_FILE`; it defaults to `token.json`.
8. Run a calibration with `--execute`. A browser opens for manual Google consent and a localhost callback completes the flow.
9. The refreshed token is reused on later runs.

```dotenv
GOOGLE_CALENDAR_CREDENTIALS_FILE=credentials.json
GOOGLE_CALENDAR_TOKEN_FILE=token.json
```

Both files are ignored by Git. Never paste their contents into issues, commits, screenshots, or chat. The app requests only:

- `calendar.app.created`, to create secondary calendars and manage their events;
- `calendar.calendarlist.readonly`, to find the marked laboratory calendar again.

It does not request Gmail, Drive, Contacts, or the broad all-calendars scope. Delete `token.json` to force fresh consent. To fully revoke access, remove the app from the Google Account's third-party access/security page; deleting the local token alone does not revoke a previously issued grant.

Check local state without starting first-time authentication:

```bash
calendar-anim calendar lab-info
```
