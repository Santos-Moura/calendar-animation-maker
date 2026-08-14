# Calendar Animation Maker

Calendar Animation Maker turns a video clip into pixel art rendered as Google Calendar events,
captures each Calendar week, and composes the screenshots into a video with the original audio.
The current reference workflow uses deterministic recurrence parents, resumable writes, and a
dedicated secondary calendar.

> This is an experimental art project. Google Calendar is not a video renderer and its web UI is
> not a stable public rendering API.

## How it works

```text
video clip
  -> sampled and quantized frames
  -> logical Calendar event plans
  -> recurrence/RDATE compaction
  -> guarded Google Calendar upload
  -> persistent-profile browser capture
  -> H.264 MP4 and source-audio mux
```

The local manifest and plans are vendor-independent. Google access begins only in explicitly
remote commands, and every write requires `--execute` plus confirmation.

## Requirements

- Python 3.12+
- Chromium/Chrome and Playwright for capture
- FFmpeg and FFprobe for final media
- Google Calendar Desktop OAuth credentials for remote work
- GNU Make is optional

## Setup

Create and activate a virtual environment, then install the project:

```bash
python -m venv .venv
python -m pip install -e ".[dev]"
python -m playwright install chromium
```

Place OAuth files only in ignored local paths. The defaults are `credentials.json` and
`token.json`; alternate paths can use the variables shown in `.env.example`.

## Quick start

With GNU Make:

```bash
make check
make plan-216
make preflight-216
```

`plan-216` is local. `preflight-216` reads Google Calendar but does not write. Review the generated
plans and the remote preflight before continuing:

```bash
make upload-216
make capture-216
make recompose-216
make compose-216
make mux-216
```

`upload-216` is the only target above that writes Calendar events. It remains interactive and
resumable. Capture is browser read-only; recomposition, MP4 composition, and audio mux are local.

Run `make help` for target descriptions and configurable variables.

## Direct CLI

Make is only a convenience layer. The package is always available directly:

```bash
python -m calendar_anim --help
python -m calendar_anim calendar --help
python -m calendar_anim inspect input.mp4
```

The complete direct-command workflow is in [docs/workflow.md](docs/workflow.md).

## Safety

- Use a dedicated secondary Google account and calendar.
- Never target a primary calendar.
- Never commit OAuth credentials, tokens, `.env`, or browser profiles.
- Remote writes and deletions require explicit `--execute`.
- Deterministic IDs and atomic checkpoints make interrupted uploads resumable.
- Cleanup is metadata-scoped; there is no unrestricted delete-all command.
- Rate limits back off; long-lived usage quotas checkpoint and pause instead of retrying rapidly.

See [docs/calendar-security.md](docs/calendar-security.md) before any remote operation.

## Development

```bash
make check
```

Without Make:

```bash
python -m ruff check .
python -m ruff format --check .
python -m mypy src
python -m pytest
```

Tests are offline and use fake gateways. See [docs/development.md](docs/development.md).

## Project structure

```text
src/calendar_anim/
  video/                 source inspection and frame reading
  renderer/              quantization, previews, and manifests
  calendar/
    frame_mapping/       pixel-to-event mapping
    multi_frame/         immutable plans and resumable checkpoints
    recurrence_*         compaction, upload, and read-only verification
    profiles/            isolated account/calendar ownership
    cayde_216/           supported 216-frame reference workflow
    hybrid_capture/      shared capture/composition primitives
  browser/               persistent-profile Calendar capture
tests/                   offline unit and integration tests
docs/                    current guides; completed investigations live in docs/archive
```

The project is released under the MIT License.
