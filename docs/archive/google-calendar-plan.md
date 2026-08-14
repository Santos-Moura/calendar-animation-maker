# Archived: Google Calendar integration plan

The target representation uses real events in a dedicated calendar. A local OAuth client now supports small calibration runs only; future code may batch animation events. Events remain in the user's account after the program closes and do not animate natively. Each future animation frame is stored in a consecutive week, and later Playwright navigation creates the illusion of playback.

The current mapper is deliberately experimental. It maps grid columns into weekdays and rows into time-of-day, and exports drafts only. The guarded calibration suite can upload a handful of static experiments to determine overlap behavior, useful resolution, minimum visible duration, zoom, and viewport—not thousands of animation events.

Animation drafts carry `animation_id`, `frame_index`, and `block_index`. Calibration drafts carry `generated_by`, `animation_id`, `run_id`, `pattern`, and `event_index`. Calibration cleanup requires the first three identifying properties. OAuth and a small-event gateway are implemented only for calibration; full animation batches, retry/backoff, quotas, and resumability are not.

For capture, a separate persistent browser profile will be ignored by Git. The user authenticates manually. Playwright will open weekly view, navigate to the first week, wait for stable markers, crop the calendar region, advance, and repeat. Capturing individual stable screenshots is preferable to recording transitions; screenshots can then be composed into a GIF or MP4. Cookies, tokens, profiles, and passwords must never be committed.
