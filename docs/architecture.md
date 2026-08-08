# Architecture

The package uses a small pipeline with explicit boundaries:

- `video` validates metadata, chooses source indices, reads only selected frames, crops, and resizes;
- `renderer` owns palettes, background masking, horizontal block merging, images, GIF, and manifest IO;
- Pydantic models define the stable data exchanged by those modules;
- `calendar` maps a manifest to drafts and exposes a gateway protocol plus a memory-only dry-run;
- `browser` exposes the future capture protocol independently from video processing.

Data flows from `VideoInfo` and `RenderConfig` through RGB NumPy arrays into logical blocks and an `AnimationManifest`. The manifest is vendor-independent so local approval, validation, testing, and alternative renderers do not need Google credentials or API availability.

Calendar access is behind a gateway because remote writes, OAuth, quotas, and safe deletion are infrastructure concerns. Calibration pattern builders produce an API-independent plan. `CalibrationService` coordinates idempotency and filtered cleanup through the port; fake and Google adapters implement it. `LabCalendarService` reuses a project-marked secondary calendar and refuses the primary calendar.

Multi-frame planning sits above the existing single-frame mapper. It assigns selected frames to consecutive calibrated weeks without duplicating fit, colors, full-grid fillers, summary ordering, or event generation. An immutable animation plan is separated from an atomically replaced upload state. The upload service serializes frame writes, skips completed frames, stops on failure, and limits recovery cleanup to the partial frame's metadata.

OAuth is isolated in `google_auth.py`; API translation is isolated in `google_gateway.py`; `.calendar-anim/calendar-config.json` stores only the reusable lab calendar ID, never tokens. A deletion selects `generated_by`, `animation_id`, and `run_id`, then deletes only the returned event IDs.

Browser capture is separate because it consumes already-created calendar state and has its own authentication, selectors, viewport, waiting, and screenshot concerns. It never creates events and must not automate login.
