"""Process-wide spacing for outbound requests to market-data and news providers.

Concurrent callers reserve evenly spaced start slots under a lock, so the
aggregate request rate stays at or below 1 / min_interval no matter how many
worker threads are running. The sleep happens outside the lock so waiting
callers don't serialise each other beyond their own slot.

If the provider answers "too many requests", on_rate_limit() pushes EVERY
caller's next slot out by an escalating pause (30 s, 60 s, ... capped at 10 min),
so all workers back off together instead of each retrying into the same wall.
Several workers reporting the same throttle burst count once. A successful call
(on_success) resets the escalation.
"""
import threading
import time

from ..config import settings


BACKOFF_START_SECONDS = 30.0
BACKOFF_MAX_SECONDS = 600.0


class SharedRateLimiter:
    def __init__(self, min_interval_seconds: float):
        self._min_interval = min_interval_seconds
        self._lock = threading.Lock()
        self._next_slot = 0.0
        self._pause = 0.0
        self._pause_until = 0.0

    def wait(self) -> None:
        if self._min_interval <= 0:
            return
        with self._lock:
            now = time.monotonic()
            slot = max(now, self._next_slot)
            self._next_slot = slot + self._min_interval
        delay = slot - now
        if delay > 0:
            time.sleep(delay)

    def on_rate_limit(self) -> float:
        """Called when the provider throttles us. Returns the pause now in force (seconds)."""
        with self._lock:
            now = time.monotonic()
            if now < self._pause_until:  # same burst, already backing off
                return self._pause
            self._pause = min(max(self._pause * 2, BACKOFF_START_SECONDS), BACKOFF_MAX_SECONDS)
            self._pause_until = now + self._pause
            self._next_slot = max(self._next_slot, self._pause_until)
            return self._pause

    def on_success(self) -> None:
        with self._lock:
            if time.monotonic() >= self._pause_until:
                self._pause = 0.0


OUTBOUND_LIMITER = SharedRateLimiter(settings.outbound_min_interval_seconds)
