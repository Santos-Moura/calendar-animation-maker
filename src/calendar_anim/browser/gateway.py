from datetime import date
from pathlib import Path
from typing import Protocol


class BrowserCaptureGateway(Protocol):
    def open_week(self, week_start: date) -> None: ...

    def wait_until_ready(self, week_start: date, minimum_event_count: int) -> None: ...

    def capture(self, output_path: Path) -> None: ...
