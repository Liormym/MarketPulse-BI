"""Process-wide spacing for outbound requests to market-data and news providers.

Concurrent callers reserve evenly spaced start slots under a lock, so the
aggregate request rate stays at or below 1 / min_interval no matter how many
worker threads are running. The sleep happens outside the lock so waiting
callers don't serialise each other beyond their own slot.
"""
import threading
import time

from ..config import settings


class SharedRateLimiter:
    def __init__(self, min_interval_seconds: float):
        self._min_interval = min_interval_seconds
        self._lock = threading.Lock()
        self._next_slot = 0.0

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


OUTBOUND_LIMITER = SharedRateLimiter(settings.outbound_min_interval_seconds)
