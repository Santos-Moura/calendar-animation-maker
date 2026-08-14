# Architecture

The repository has five operational boundaries.

1. **Media** — `video` reads selected frames; `renderer` fits, quantizes, and writes a manifest.
2. **Mapping** — `calendar/frame_mapping` converts logical pixels into deterministic Calendar
   drafts. Synchronized horizontal bands reduce event count without changing the logical grid.
3. **Planning and upload** — `multi_frame` owns immutable frame plans and checkpoints;
   `recurrence_compaction` groups equivalent occurrences into bounded RDATE parents;
   `recurrence_upload` owns resumable remote writes and quota handling.
4. **Capture** — `browser` controls an already-authenticated persistent Chrome profile;
   `hybrid_capture` contains the shared grid discovery, readiness, crop, and composition primitives
   used by the supported `cayde_216` workflow. Despite the historical module name, these primitives
   are not limited to the retired two-account strategy.
5. **Media output** — captured PNGs are recomposed locally, encoded with FFmpeg, then muxed with an
   exact source-audio clip.

Pydantic models are persisted between stages. Immutable plans are separate from mutable state;
state files are replaced atomically after each successful unit of work.

## Safety invariants

- Calendar resources use deterministic IDs and private ownership metadata.
- Recurrence expansion must equal the original occurrence plan before upload.
- Account profiles bind a token, owned secondary calendar, and browser profile.
- Writes are serial, resumable, and explicitly enabled.
- Temporary rate limits back off; usage quotas checkpoint and pause.
- Browser capture never creates, updates, or deletes Calendar events.
- Media composition never opens Calendar or mutates capture checkpoints.

The Google API and Playwright implementations sit behind gateways so core planning and tests stay
offline. Fake gateways are the default test boundary.
