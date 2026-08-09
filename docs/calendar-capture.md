# Google Calendar week capture

## Purpose and boundary

Capture turns uploaded Calendar weeks into stable screenshots and composes those files into an
animation:

```text
animation-plan.json + completed animation-state.json
    -> immutable capture-plan.json
    -> headed installed Chrome with a dedicated persistent profile
    -> one stable screenshot per persisted week
    -> atomic capture-state.json checkpoint
    -> GIF and optional H.264 MP4
```

The browser layer only navigates, waits, validates visual state, and captures. It never calls the
Calendar API and never creates, updates, or deletes events. Login is manual; credentials, passwords,
OAuth files, and browser cookies are never read by the capture code.

## Install Playwright explicitly

Install the updated project dependencies. The default capture channel uses an existing Google
Chrome installation:

```powershell
python -m pip install -e ".[dev]"
```

Bundled Chromium remains an explicit fallback and is never downloaded by project code:

```powershell
python -m playwright install chromium
python -m calendar_anim calendar capture-animation --run-id animation-test-01 --browser-channel bundled-chromium --execute
```

Google may reject interactive sign-in from software-controlled browsers, so authentication uses
normal Chrome and the default capture uses Playwright's `chrome` channel. The browser profile is
stored below `.calendar-anim/browser-profile/`, ignored by Git, and separate from a normal profile.

## One-time manual login and visual setup

```powershell
python -m calendar_anim calendar browser-login
```

The command starts normal Chrome without Playwright or a remote-debugging connection. Log in
manually and set Google Calendar to the calibrated visual state:

- week view;
- browser zoom at 100%;
- 1920x1080 viewport;
- dark theme;
- sidebar hidden;
- only the `Calendar Animation Lab` calendar visible when consistency requires it.

Close that Chrome window completely, then return to the terminal and press Enter. The command stores
browser session data in the ignored profile directory; it does not know or type the credentials.

## Plan without opening a browser

The upload must already report every selected frame as `completed`. Then run:

```powershell
python -m calendar_anim calendar capture-animation --run-id animation-test-01
```

This is a local dry-run. It reads the immutable weeks from
`output/animation-runs/animation-test-01/animation-plan.json`, verifies the corresponding upload
state, and creates:

```text
output/captures/animation-test-01/
|-- capture-plan.json
|-- capture-state.json
`-- capture-report.txt
```

Capture never recalculates frame dates. The SHA-256 digest of the source animation plan is persisted
so a capture run cannot silently drift to another upload plan.

## Capture and resume

```powershell
python -m calendar_anim calendar capture-animation --run-id animation-test-01 --execute
```

The browser opens each planned week directly. For each frame the adapter:

1. verifies that the Calendar URL represents the exact persisted week;
2. waits for a visible main Calendar region;
3. waits for at least one event marker when the frame planned events;
4. finds the large vertical Calendar time scroller and positions it at 06:00;
5. verifies the scroll offset and that the viewport can contain 06:00-18:00;
6. crops the Calendar region at the calculated 18:00 boundary;
7. compares consecutive cropped snapshots until the configured stable count is reached;
8. atomically checkpoints the frame as `completed`.

Statuses are `pending`, `capturing`, `completed`, and `failed`. Re-running the same command skips a
completed frame only when its screenshot still exists. A failed or interrupted frame is retried;
already completed screenshots are preserved. A missing screenshot behind a completed checkpoint is
treated as an inconsistency instead of being silently recaptured.

To intentionally replace a completed capture, use the explicit recapture flag:

```powershell
python -m calendar_anim calendar capture-animation --run-id animation-test-01 --recapture --execute
```

Before resetting the checkpoints, this copies existing PNGs plus any GIF/MP4 into
`output/captures/animation-test-01/backups/<timestamp>/`. A failure during the new capture therefore
does not destroy the previously composed result.

The defaults are a 30-second ready timeout and two stable snapshots separated by two seconds. They
can be fixed in the dry-run plan and reused during execution:

```powershell
python -m calendar_anim calendar capture-animation --run-id animation-test-01 --stabilization-seconds 3 --ready-timeout-seconds 60
python -m calendar_anim calendar capture-animation --run-id animation-test-01 --stabilization-seconds 3 --ready-timeout-seconds 60 --execute
```

Because the capture plan is immutable, changing these visual settings for the same run is rejected.
Use a new capture output root when intentionally testing a different capture configuration.

## Compose the screenshots

GIF composition is local and preserves planned order and the full duration of repeated consecutive
screenshots:

```powershell
python -m calendar_anim calendar compose-capture --run-id animation-test-01 --fps 3
Invoke-Item .\output\captures\animation-test-01\animation.gif
```

Request an additional H.264 MP4 only when `ffmpeg` is already installed and on `PATH`:

```powershell
python -m calendar_anim calendar compose-capture --run-id animation-test-01 --fps 3 --mp4
Invoke-Item .\output\captures\animation-test-01\animation.mp4
```

Composition refuses incomplete state, missing images, or inconsistent screenshot dimensions. MP4
uses consecutive `frame-NNNN.png` files and pads odd dimensions to satisfy `yuv420p`.

## Full artifact layout

```text
output/captures/animation-test-01/
|-- capture-plan.json
|-- capture-state.json
|-- capture-report.txt
|-- animation.gif
|-- animation.mp4              # only with --mp4
`-- frames/
    |-- frame-0000.png
    |-- frame-0001.png
    `-- ...
```

## Selector and UI limitations

Google Calendar is a third-party web UI, not a stable API contract. The adapter deliberately avoids
matching the exact number of event DOM nodes because Calendar virtualizes and re-renders content.
It instead uses broad main-region and event-marker selectors plus snapshot stability. Google may
change these selectors or redirect week URLs; those failures are surfaced and checkpointed instead
of producing an unvalidated screenshot.

The code fixes viewport, device scale factor, color scheme preference, week URL, zoom, timeout,
06:00 scroll position, 18:00 crop boundary, and stabilization centrally. Calendar-specific theme,
sidebar visibility, and selected calendars remain part of the manual calibrated profile. Visually
inspect the first corrected screenshot before composing a long run.
