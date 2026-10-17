"""In-process sliding-window rate limits. One API process; several would need a shared store."""

import time
from collections import defaultdict, deque
from collections.abc import Callable
from dataclasses import dataclass

from app.core.config import Settings


class SlidingWindowLimiter:
    """At most `limit` hits per key in any `window_seconds` window."""

    def __init__(
        self, limit: int, window_seconds: float = 60, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self.limit = limit
        self.window = window_seconds
        self._clock = clock
        self._hits: defaultdict[str, deque[float]] = defaultdict(deque)

    def hit(self, key: str) -> float | None:
        """Record a hit. Returns None if allowed, or the seconds to wait if over the limit."""
        now = self._clock()
        hits = self._hits[key]
        while hits and hits[0] <= now - self.window:
            hits.popleft()
        if len(hits) >= self.limit:
            return self.window - (now - hits[0])
        hits.append(now)
        return None


@dataclass(frozen=True, slots=True)
class RateLimits:
    chat: SlidingWindowLimiter
    login: SlidingWindowLimiter

    @classmethod
    def from_settings(cls, settings: Settings) -> "RateLimits":
        return cls(
            chat=SlidingWindowLimiter(settings.chat_rate_limit_per_minute),
            login=SlidingWindowLimiter(settings.login_rate_limit_per_minute),
        )
