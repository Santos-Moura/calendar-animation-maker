import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from random import Random
from typing import Any, Final

from googleapiclient.errors import HttpError


@dataclass(frozen=True)
class UploadRetryPolicy:
    max_event_attempts: int = 5
    base_delay_seconds: float = 2.0
    max_delay_seconds: float = 30.0
    jitter_seconds: float = 1.0
    max_frame_recovery_cycles: int = 3
    rate_limit_base_delay_seconds: float = 32.0
    rate_limit_max_delay_seconds: float = 64.0

    def __post_init__(self) -> None:
        if self.max_event_attempts < 1:
            raise ValueError("max_event_attempts must be at least 1")
        if self.base_delay_seconds < 0 or self.max_delay_seconds < 0:
            raise ValueError("retry delays must be non-negative")
        if self.jitter_seconds < 0:
            raise ValueError("retry jitter must be non-negative")
        if self.max_frame_recovery_cycles < 0:
            raise ValueError("max_frame_recovery_cycles must be non-negative")
        if self.rate_limit_base_delay_seconds < 0 or self.rate_limit_max_delay_seconds < 0:
            raise ValueError("rate-limit delays must be non-negative")


DEFAULT_UPLOAD_RETRY_POLICY: Final = UploadRetryPolicy()
RETRYABLE_FORBIDDEN_REASONS: Final = frozenset({"rateLimitExceeded", "userRateLimitExceeded"})
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


def rate_limit_retry_delay(
    policy: UploadRetryPolicy,
    failed_attempt: int,
    jitter: Jitter = default_jitter,
) -> float:
    exponential = policy.rate_limit_base_delay_seconds * (2 ** max(0, failed_attempt - 1))
    base = min(policy.rate_limit_max_delay_seconds, exponential)
    available_jitter = min(
        policy.jitter_seconds,
        max(0.0, policy.rate_limit_max_delay_seconds - base),
    )
    delayed: float = base + jitter(available_jitter)
    return min(policy.rate_limit_max_delay_seconds, delayed)


def is_retryable_exception(error: BaseException) -> bool:
    if isinstance(error, HttpError):
        status = int(getattr(error.resp, "status", 0) or 0)
        return (
            status == 429
            or 500 <= status <= 599
            or status == 403
            and bool(http_error_reasons(error) & RETRYABLE_FORBIDDEN_REASONS)
        )
    if isinstance(error, (TimeoutError, ConnectionError)):
        return True
    module = type(error).__module__
    name = type(error).__name__.lower()
    return module.startswith(("httplib2", "http.client")) and any(
        marker in name for marker in ("timeout", "server", "connection", "socket")
    )


def is_rate_limit_exception(error: BaseException) -> bool:
    if not isinstance(error, HttpError):
        return False
    status = int(getattr(error.resp, "status", 0) or 0)
    return status == 429 or bool(http_error_reasons(error) & RETRYABLE_FORBIDDEN_REASONS)


def http_error_reasons(error: HttpError) -> set[str]:
    reasons: set[str] = set()
    details = getattr(error, "error_details", None)
    if isinstance(details, list):
        reasons.update(_reasons_from_details(details))
    content = getattr(error, "content", b"")
    try:
        payload = json.loads(content.decode("utf-8") if isinstance(content, bytes) else content)
    except (AttributeError, json.JSONDecodeError, TypeError, UnicodeDecodeError):
        return reasons
    if not isinstance(payload, Mapping):
        return reasons
    error_payload = payload.get("error")
    if isinstance(error_payload, Mapping):
        errors = error_payload.get("errors")
        if isinstance(errors, list):
            reasons.update(_reasons_from_details(errors))
    return reasons


def _reasons_from_details(details: list[Any]) -> set[str]:
    return {
        str(detail["reason"])
        for detail in details
        if isinstance(detail, Mapping) and detail.get("reason")
    }
