from pathlib import Path

import pytest
from typer.testing import CliRunner

from calendar_anim.cli import app

pytestmark = pytest.mark.integration
runner = CliRunner()


def test_profile_management_dry_runs_never_authenticate_or_write_google(tmp_path: Path) -> None:
    profiles_root = tmp_path / "profiles"
    common = ["--profiles-root", str(profiles_root)]

    initialized = runner.invoke(
        app,
        [
            "calendar",
            "profiles",
            "init",
            "--profile",
            "account-b",
            "--calendar-name",
            "Calendar Animation Lab B",
            *common,
        ],
    )
    listed = runner.invoke(app, ["calendar", "profiles", "list", *common])
    authenticated = runner.invoke(
        app,
        ["calendar", "profiles", "auth", "--profile", "account-b", *common],
    )
    inspected = runner.invoke(
        app,
        ["calendar", "profiles", "inspect", "--profile", "account-b", *common],
    )
    created = runner.invoke(
        app,
        [
            "calendar",
            "profiles",
            "create-calendar",
            "--profile",
            "account-b",
            "--name",
            "Calendar Animation Lab B",
            *common,
        ],
    )

    for result in (initialized, listed, authenticated, inspected, created):
        assert result.exit_code == 0, result.output
    assert "Google calls: NO" in initialized.output
    assert "account-a" in listed.output
    assert "account-b" in listed.output
    assert "No browser was opened" in authenticated.output
    assert "Authenticated Google account: NO" in inspected.output
    assert "Capture zoom: 90%" in inspected.output
    assert "Google Calendar writes: NO" in inspected.output
    assert "EXECUTION: DRY RUN" in created.output
    assert not (profiles_root / "account-b/token.json").exists()
