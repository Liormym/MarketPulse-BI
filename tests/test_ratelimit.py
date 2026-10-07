import threading
import time

from marketpulse.extract.ratelimit import SharedRateLimiter


def test_zero_interval_never_blocks():
    limiter = SharedRateLimiter(0)
    start = time.monotonic()
    for _ in range(50):
        limiter.wait()
    assert time.monotonic() - start < 0.05


def test_sequential_calls_are_spaced_by_the_interval():
    limiter = SharedRateLimiter(0.05)
    start = time.monotonic()
    for _ in range(4):
        limiter.wait()
    assert time.monotonic() - start >= 0.15 * 0.9


def test_concurrent_callers_share_one_rate():
    interval = 0.05
    n = 8
    limiter = SharedRateLimiter(interval)
    released = []
    lock = threading.Lock()

    def call():
        limiter.wait()
        with lock:
            released.append(time.monotonic())

    threads = [threading.Thread(target=call) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    span = max(released) - min(released)
    assert span >= (n - 1) * interval * 0.8
