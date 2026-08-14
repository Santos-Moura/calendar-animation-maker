from pathlib import Path
from typing import Any

import pytest

from calendar_anim.calendar.local_config import CalendarLocalConfig
from calendar_anim.calendar.models import CalendarInfo
from calendar_anim.calendar.multi_frame.models import MultiFramePlan
from calendar_anim.calendar.profiles.service import CalendarProfileService
from calendar_anim.calendar.profiles.store import (
    CalendarProfileStore,
    ProfileCalendarConfigStore,
)
from calendar_anim.exceptions import CalendarAnimError


class FakeOAuthClient:
    configs: list[Any] = []

    def __init__(self, config: Any) -> None:
        self.config = config
        self.configs.append(config)

    def build_service(self) -> object:
        self.config.token_file.parent.mkdir(parents=True, exist_ok=True)
        self.config.token_file.write_text("account-b-token", encoding="utf-8")
        return object()


class PrimaryGateway:
    primary_id = "secondary@example.com"

    def __init__(self, _service: object) -> None:
        pass

    def get_calendar(self, calendar_id: str) -> CalendarInfo | None:
        if calendar_id == "primary":
            return CalendarInfo(
                id=self.primary_id,
                name=self.primary_id,
                timezone="America/Sao_Paulo",
                primary=True,
            )
        return None


def profile_store(tmp_path: Path) -> CalendarProfileStore:
    return CalendarProfileStore(
        root=tmp_path / ".calendar-anim/profiles",
        legacy_calendar_config=tmp_path / ".calendar-anim/calendar-config.json",
    )


def test_two_profiles_use_separate_tokens_and_calendar_configs(tmp_path: Path) -> None:
    store = profile_store(tmp_path)
    account_a = store.initialize("account-a", calendar_name="Calendar Animation Lab")
    account_b = store.initialize("account-b", calendar_name="Calendar Animation Lab B")

    assert account_a.token_file == Path("token.json")
    assert account_b.token_file == tmp_path / ".calendar-anim/profiles/account-b/token.json"
    assert account_a.token_file != account_b.token_file
    assert account_a.capture_zoom_percent == 33
    assert account_b.capture_zoom_percent == 90

    a_calendar = ProfileCalendarConfigStore(store, "account-a")
    b_calendar = ProfileCalendarConfigStore(store, "account-b")
    a_calendar.save(CalendarLocalConfig(lab_calendar_id="calendar-a", lab_calendar_name="A"))
    b_calendar.save(CalendarLocalConfig(lab_calendar_id="calendar-b", lab_calendar_name="B"))

    assert a_calendar.load().lab_calendar_id == "calendar-a"
    assert b_calendar.load().lab_calendar_id == "calendar-b"
    assert store.load("account-a").calendar_id != store.load("account-b").calendar_id


def test_auth_b_writes_only_b_token_and_records_selected_account(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    store = profile_store(tmp_path)
    store.initialize("account-b", calendar_name="Calendar Animation Lab B")
    account_a_token = tmp_path / "token.json"
    account_a_token.write_text("do-not-touch-account-a", encoding="utf-8")
    FakeOAuthClient.configs.clear()
    service = CalendarProfileService(
        store,
        oauth_client_factory=FakeOAuthClient,  # type: ignore[arg-type]
        gateway_factory=PrimaryGateway,  # type: ignore[arg-type]
    )

    profile, _gateway = service.authenticate("account-b")

    assert account_a_token.read_text(encoding="utf-8") == "do-not-touch-account-a"
    assert profile.token_file.read_text(encoding="utf-8") == "account-b-token"
    assert profile.authenticated_google_account == "secondary@example.com"
    assert FakeOAuthClient.configs[-1].token_file == profile.token_file


def test_gateway_refuses_token_from_a_different_google_account(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    store = profile_store(tmp_path)
    profile = store.initialize("account-b", calendar_name="Calendar Animation Lab B")
    profile.authenticated_google_account = "expected@example.com"
    profile.token_file.parent.mkdir(parents=True, exist_ok=True)
    profile.token_file.write_text("token", encoding="utf-8")
    store.save(profile)
    service = CalendarProfileService(
        store,
        oauth_client_factory=FakeOAuthClient,  # type: ignore[arg-type]
        gateway_factory=PrimaryGateway,  # type: ignore[arg-type]
    )

    with pytest.raises(CalendarAnimError, match="Wrong Google account"):
        service.gateway("account-b")


def test_legacy_plan_without_profile_loads_as_account_a() -> None:
    payload = {
        "animation_id": "legacy",
        "run_id": "legacy-run",
        "timezone": "America/Sao_Paulo",
        "start_week": "2027-01-03",
        "frame_start": 0,
        "frame_count": 1,
        "mapping_mode": "full-grid",
        "event_compression": "none",
        "target_grid_width": 7,
        "target_grid_height": 1,
        "subcolumn_order_strategy": "none",
        "max_events_per_frame": 1,
        "profile_ready": True,
        "events_per_frame": [1],
        "total_events": 1,
        "frames": [
            {
                "frame_index": 0,
                "week_start": "2027-01-03",
                "frame_run_id": "legacy-frame",
                "planned_events": 1,
                "artifact_directory": "frames/frame-0000",
            }
        ],
    }

    plan = MultiFramePlan.model_validate(payload)

    assert plan.calendar_profile == "account-a"
    assert plan.frames[0].calendar_profile is None


def test_legacy_profile_json_gets_profile_specific_capture_zoom(tmp_path: Path) -> None:
    store = profile_store(tmp_path)
    profile_path = store.profile_path("account-b")
    profile_path.parent.mkdir(parents=True)
    profile_path.write_text(
        """{
  "profile_name": "account-b",
  "credentials_file": "credentials.json",
  "token_file": ".calendar-anim/profiles/account-b/token.json",
  "calendar_name": "Calendar Animation Lab B",
  "browser_profile_directory": ".calendar-anim/browser-profiles/account-b"
}
""",
        encoding="utf-8",
    )

    profile = store.load("account-b")

    assert profile.capture_zoom_percent == 90


def test_profile_runtime_paths_reject_workspace_escape(tmp_path: Path) -> None:
    store = profile_store(tmp_path)
    with pytest.raises(CalendarAnimError, match="Invalid Calendar profile"):
        store.load("../account-b")
