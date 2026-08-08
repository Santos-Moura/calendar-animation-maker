from pathlib import Path

import pytest

from calendar_anim.calendar.fake import FakeCalendarGateway
from calendar_anim.calendar.lab import LabCalendarService
from calendar_anim.calendar.local_config import CalendarConfigStore
from calendar_anim.calendar.multi_frame.cleanup import MultiFrameCleanupService
from calendar_anim.calendar.multi_frame.models import FrameUploadStatus
from calendar_anim.calendar.multi_frame.service import MultiFrameUploadService
from tests.integration.test_multi_frame_upload import _initialized_run

pytestmark = pytest.mark.integration


def _uploaded_run(tmp_path: Path):
    plan, state, store = _initialized_run(tmp_path, frame_count=3)
    gateway = FakeCalendarGateway()
    lab = LabCalendarService(gateway, CalendarConfigStore(tmp_path / "calendar.json"))
    state = MultiFrameUploadService(gateway, lab, store, chunk_size=100).upload(plan, state)
    return plan, state, store, gateway, lab


def test_cleanup_one_frame_preserves_other_frames(tmp_path: Path) -> None:
    plan, state, store, gateway, lab = _uploaded_run(tmp_path)
    service = MultiFrameCleanupService(lab, store)
    match = service.find_matches(plan, frame_index=1)

    result = service.cleanup(plan, state, match)

    assert result.selected_frames == [1]
    assert result.matched_events == 1008
    assert result.deleted_events == 1008
    saved = store.load_state(plan.run_id)
    assert [frame.status for frame in saved.frames] == [
        FrameUploadStatus.COMPLETED,
        FrameUploadStatus.PENDING,
        FrameUploadStatus.COMPLETED,
    ]
    remaining_frames = {
        event.private_metadata["frame_index"] for event in gateway.events[state.calendar_id or ""]
    }
    assert remaining_frames == {"0", "2"}


def test_cleanup_entire_animation_resets_all_frames(tmp_path: Path) -> None:
    plan, state, store, gateway, lab = _uploaded_run(tmp_path)
    service = MultiFrameCleanupService(lab, store)
    match = service.find_matches(plan)

    result = service.cleanup(plan, state, match)

    assert result.matched_events == 3 * 1008
    assert result.deleted_events == 3 * 1008
    assert all(
        frame.status is FrameUploadStatus.PENDING for frame in store.load_state(plan.run_id).frames
    )
    assert gateway.events[state.calendar_id or ""] == []
