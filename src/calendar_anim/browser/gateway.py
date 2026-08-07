from datetime import date
from pathlib import Path
from typing import Protocol


class BrowserCaptureGateway(Protocol):
    def capture_frames(
        self, start_week: date, frame_count: int, output_dir: Path
    ) -> list[Path]: ...


class PlaywrightCaptureGateway:
    """Disabled until selectors and visual calibration have been validated."""

    def capture_frames(self, start_week: date, frame_count: int, output_dir: Path) -> list[Path]:
        raise NotImplementedError("Playwright capture is planned but not implemented")
