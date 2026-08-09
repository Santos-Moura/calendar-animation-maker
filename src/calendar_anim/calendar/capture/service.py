from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from calendar_anim.browser.gateway import BrowserCaptureGateway
from calendar_anim.calendar.capture.artifacts import CaptureStore
from calendar_anim.calendar.capture.models import CapturePlan, CaptureState, FrameCaptureStatus
from calendar_anim.exceptions import CalendarAnimError

CaptureProgress = Callable[[int, FrameCaptureStatus], None]


class CalendarWeekCaptureService:
    def __init__(
        self,
        gateway: BrowserCaptureGateway,
        store: CaptureStore,
        progress: CaptureProgress | None = None,
    ) -> None:
        self.gateway = gateway
        self.store = store
        self.progress = progress or (lambda _index, _status: None)

    def capture(self, plan: CapturePlan, state: CaptureState) -> CaptureState:
        for frame_plan in plan.frames:
            frame_state = state.frame(frame_plan.frame_index)
            screenshot = self.store.screenshot_path(plan, frame_plan.frame_index)
            if frame_state.status is FrameCaptureStatus.COMPLETED:
                if not screenshot.is_file():
                    raise CalendarAnimError(
                        f"Completed capture is missing its screenshot: {screenshot}"
                    )
                self.progress(frame_plan.frame_index, FrameCaptureStatus.COMPLETED)
                continue
            frame_state.status = FrameCaptureStatus.CAPTURING
            frame_state.started_at = datetime.now(UTC)
            frame_state.completed_at = None
            frame_state.error = None
            self.store.save_state(state)
            self.store.save_report(plan, state)
            self.progress(frame_plan.frame_index, FrameCaptureStatus.CAPTURING)
            try:
                self.gateway.open_week(frame_plan.week_start)
                self.gateway.wait_until_ready(frame_plan.week_start, frame_plan.planned_events)
                screenshot.parent.mkdir(parents=True, exist_ok=True)
                self.gateway.capture(screenshot)
                if not screenshot.is_file() or screenshot.stat().st_size == 0:
                    raise CalendarAnimError(f"Browser did not create screenshot: {screenshot}")
            except Exception as error:
                frame_state.status = FrameCaptureStatus.FAILED
                frame_state.error = str(error)
                self.store.save_state(state)
                self.store.save_report(plan, state)
                self.progress(frame_plan.frame_index, FrameCaptureStatus.FAILED)
                raise
            frame_state.status = FrameCaptureStatus.COMPLETED
            frame_state.completed_at = datetime.now(UTC)
            self.store.save_state(state)
            self.store.save_report(plan, state)
            self.progress(frame_plan.frame_index, FrameCaptureStatus.COMPLETED)
        return state


def captured_paths(plan: CapturePlan, store: CaptureStore) -> list[Path]:
    return [store.screenshot_path(plan, frame.frame_index) for frame in plan.frames]
