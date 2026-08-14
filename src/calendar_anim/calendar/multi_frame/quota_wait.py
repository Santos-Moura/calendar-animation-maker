from dataclasses import dataclass


@dataclass(frozen=True)
class QuotaWaitPolicy:
    cooldown_seconds: tuple[float, ...]
    jitter_seconds: float
    max_auto_wait_seconds: float
    conservative_recovery_interval_seconds: float

    def __post_init__(self) -> None:
        if not self.cooldown_seconds or any(value <= 0 for value in self.cooldown_seconds):
            raise ValueError("quota cooldowns must be positive")
        if self.jitter_seconds < 0:
            raise ValueError("quota jitter must be non-negative")
        if self.max_auto_wait_seconds <= 0:
            raise ValueError("maximum automatic quota wait must be positive")
        if self.conservative_recovery_interval_seconds < 0:
            raise ValueError("recovery write interval must be non-negative")

    def cooldown_for_stage(self, stage_index: int) -> float:
        return self.cooldown_seconds[min(stage_index, len(self.cooldown_seconds) - 1)]
