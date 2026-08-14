# Development

Use Python 3.12 or newer. Tests do not need Google credentials, a browser session, or network
access.

```bash
python -m venv .venv
python -m pip install -e ".[dev]"
make check
```

If GNU Make is unavailable (common on a default Windows installation), activate the virtual
environment and run the same checks directly:

```bash
python -m ruff check .
python -m ruff format --check .
python -m mypy src
python -m pytest
```

Use `python -m ruff format .` for intentional formatting changes. Add dependencies only when they
serve a tested runtime or development boundary.

## Test boundaries

- Unit tests cover mapping, recurrence equality, deterministic IDs, resume, quota handling,
  capture geometry, and media validation.
- Integration tests exercise the CLI and filesystem through temporary directories.
- Google adapters are replaced by fakes; tests must never discover local OAuth files or contact
  Google merely because credentials happen to exist on the machine.

Do not commit input media, generated output, OAuth files, `.env`, `.calendar-anim/`, browser
profiles, screenshots, or performance reports.

## Change discipline

- Keep remote operations dry-run/read-only by default where the command contract permits it.
- Preserve deterministic identity and atomic checkpoint invariants.
- Add focused tests for every safety or recovery change.
- Run `make check` before opening a pull request.
