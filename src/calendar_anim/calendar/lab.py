from typing import Final

from calendar_anim.calendar.gateway import CalendarGateway
from calendar_anim.calendar.local_config import CalendarConfigStore, CalendarLocalConfig
from calendar_anim.calendar.models import CalendarInfo
from calendar_anim.exceptions import CalendarAnimError

LAB_CALENDAR_DESCRIPTION: Final = (
    "Dedicated calendar used by calendar-anim for safe visual calibration "
    "and animation experiments."
)


class LabCalendarService:
    def __init__(self, gateway: CalendarGateway, store: CalendarConfigStore) -> None:
        self.gateway = gateway
        self.store = store

    def find(self, name: str) -> CalendarInfo | None:
        config = self.store.load()
        if config.lab_calendar_id:
            calendar = self.gateway.get_calendar(config.lab_calendar_id)
            if calendar:
                self._validate(calendar)
                return calendar
        calendar = self.gateway.find_calendar(name, LAB_CALENDAR_DESCRIPTION)
        if calendar:
            self._validate(calendar)
            self._remember(calendar)
        return calendar

    def resolve(self, name: str, timezone: str) -> tuple[CalendarInfo, bool]:
        existing = self.find(name)
        if existing:
            return existing, False
        calendar = self.gateway.create_calendar(name, LAB_CALENDAR_DESCRIPTION, timezone)
        self._validate(calendar)
        self._remember(calendar)
        return calendar, True

    @staticmethod
    def _validate(calendar: CalendarInfo) -> None:
        if calendar.primary or calendar.id == "primary":
            raise CalendarAnimError("Refusing to use the primary Google Calendar")
        if calendar.description != LAB_CALENDAR_DESCRIPTION:
            raise CalendarAnimError(
                "Configured calendar is not recognized as a calendar-anim laboratory calendar"
            )

    def _remember(self, calendar: CalendarInfo) -> None:
        self.store.save(
            CalendarLocalConfig(lab_calendar_id=calendar.id, lab_calendar_name=calendar.name)
        )
