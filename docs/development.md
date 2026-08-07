# Development

Requirements: Python 3.12+, `uv` preferred, and Git. No account, browser, internet, or credentials are needed for tests.

```bash
uv sync --extra dev
uv run calendar-anim --help
uv run pytest
uv run pytest tests/unit
uv run pytest tests/integration
uv run ruff check .
uv run ruff format --check .
uv run mypy
```

With pip:

```bash
python -m venv .venv
python -m pip install -e ".[dev]"
python -m calendar_anim --help
pytest
pytest tests/unit
pytest tests/integration
ruff check .
ruff format --check .
mypy
```

Tests generate a tiny AVI fixture programmatically. Do not add personal videos, generated output, credentials, OAuth tokens, or Playwright profiles. Add dependencies only when they serve the local pipeline or a tested integration boundary.

Calendar tests use `FakeCalendarGateway`; they never open OAuth, access the network, or require a Google account. CLI dry-run tests assert that missing credentials are harmless until `--execute` is explicitly supplied.

## Test boundaries

- `tests/unit` contains fast tests for sampling, crop/resize, palette, background masking, block merging, weekly mapping, and the in-memory dry-run gateway. They do not open real videos or invoke the CLI.
- `tests/integration` combines components through temporary files, a generated AVI, OpenCV, manifest IO, and Typer's in-process CLI runner. The render test covers the complete local workflow, but it is not a full system E2E test because it does not start a separate process or contact Google Calendar/Playwright.
- `tests/conftest.py` builds the tiny video fixture; `tests/factories.py` builds valid domain models shared by both suites.
