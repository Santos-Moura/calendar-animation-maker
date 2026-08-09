from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from calendar_anim.browser.login import (
    CALENDAR_LOGIN_URL,
    find_chrome_executable,
    launch_manual_login_browser,
)
from calendar_anim.exceptions import CalendarAnimError

pytestmark = pytest.mark.unit


def test_manual_login_launches_normal_chrome_without_playwright(tmp_path: Path) -> None:
    chrome = tmp_path / "chrome.exe"
    chrome.touch()
    profile = tmp_path / "profile"
    process = Mock()

    with patch("calendar_anim.browser.login.subprocess.Popen", return_value=process) as popen:
        result = launch_manual_login_browser(profile, chrome)

    assert result is process
    command = popen.call_args.args[0]
    assert command[0] == str(chrome.resolve())
    assert f"--user-data-dir={profile.resolve()}" in command
    assert "--disable-background-mode" in command
    assert CALENDAR_LOGIN_URL in command
    assert not any("remote-debugging" in argument for argument in command)


def test_explicit_browser_executable_must_exist(tmp_path: Path) -> None:
    with pytest.raises(CalendarAnimError, match="does not exist"):
        find_chrome_executable(tmp_path / "missing-chrome.exe")
