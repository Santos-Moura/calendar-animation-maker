import os
import shutil
import subprocess
from pathlib import Path

from calendar_anim.exceptions import CalendarAnimError

CALENDAR_LOGIN_URL = "https://calendar.google.com/calendar/u/0/r/week"


def find_chrome_executable(explicit: Path | None = None) -> Path:
    if explicit is not None:
        resolved = explicit.expanduser().resolve()
        if not resolved.is_file():
            raise CalendarAnimError(f"Chrome executable does not exist: {resolved}")
        return resolved
    command = shutil.which("chrome") or shutil.which("chrome.exe")
    candidates = [Path(command)] if command else []
    for environment_name, relative in (
        ("PROGRAMFILES", "Google/Chrome/Application/chrome.exe"),
        ("PROGRAMFILES(X86)", "Google/Chrome/Application/chrome.exe"),
        ("LOCALAPPDATA", "Google/Chrome/Application/chrome.exe"),
    ):
        root = os.environ.get(environment_name)
        if root:
            candidates.append(Path(root) / relative)
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    raise CalendarAnimError(
        "Google Chrome was not found. Install Chrome or pass --browser-executable."
    )


def launch_manual_login_browser(
    profile_directory: Path,
    browser_executable: Path | None = None,
) -> subprocess.Popen[bytes]:
    executable = find_chrome_executable(browser_executable)
    profile = profile_directory.expanduser().resolve()
    profile.mkdir(parents=True, exist_ok=True)
    command = [
        str(executable),
        f"--user-data-dir={profile}",
        "--disable-background-mode",
        "--no-first-run",
        CALENDAR_LOGIN_URL,
    ]
    try:
        return subprocess.Popen(command)
    except OSError as error:
        raise CalendarAnimError(f"Could not open Google Chrome: {error}") from error
