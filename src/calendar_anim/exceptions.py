class CalendarAnimError(Exception):
    """Base class for expected, user-facing errors."""


class VideoValidationError(CalendarAnimError):
    """The input video or requested clip is invalid."""


class ManifestValidationError(CalendarAnimError):
    """An animation manifest is invalid or inconsistent."""


class IntegrationNotConfiguredError(CalendarAnimError):
    """An intentionally disabled external integration was requested."""
