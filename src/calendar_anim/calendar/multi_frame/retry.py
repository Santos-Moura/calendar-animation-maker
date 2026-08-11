from collections.abc import Callable
from dataclasses import dataclass
from random import Random
from typing import Final

from googleapiclient.errors import HttpError


@dataclass(frozen=True)
class UploadRetryPolicy:
    max_event_attempts: int = 5
    base_delay_seconds: float = 2.0
    max_delay_seconds: float = 30.0
    jitter_seconds: float = 1.0
    max_frame_recovery_cycles: int = 3

    def __post_init__(self) -> None:
        if self.max_event_attempts < 1:
            raise ValueError("max_event_attempts must be at least 1")
        if self.base_delay_seconds < 0 or self.max_delay_seconds < 0:
            raise ValueError("retry delays must be non-negative")
        if self.jitter_seconds < 0:
            raise ValueError("retry jitter must be non-negative")
        if self.max_frame_recovery_cycles < 0:
            raise ValueError("max_frame_recovery_cycles must be non-negative")


DEFAULT_UPLOAD_RETRY_POLICY: Final = UploadRetryPolicy()
type Jitter = Callable[[float], float]


def default_jitter(maximum: float) -> float:
    return float(Random().uniform(0.0, maximum))


def retry_delay(
    policy: UploadRetryPolicy,
    failed_attempt: int,
    jitter: Jitter = default_jitter,
) -> float:
    exponential = policy.base_delay_seconds * (2 ** max(0, failed_attempt - 1))
    base = min(policy.max_delay_seconds, exponential)
    available_jitter = min(policy.jitter_seconds, max(0.0, policy.max_delay_seconds - base))
    jitter_value: float = jitter(available_jitter)
    delayed: float = base + jitter_value
    return policy.max_delay_seconds if delayed > policy.max_delay_seconds else delayed


def is_retryable_exception(error: BaseException) -> bool:
    if isinstance(error, HttpError):
        status = int(getattr(error.resp, "status", 0) or 0)
        return status == 429 or 500 <= status <= 599
    if isinstance(error, (TimeoutError, ConnectionError)):
        return True
    module = type(error).__module__
    name = type(error).__name__.lower()
    return module.startswith(("httplib2", "http.client")) and any(
        marker in name for marker in ("timeout", "server", "connection", "socket")
    )
