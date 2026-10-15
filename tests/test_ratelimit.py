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


def test_rate_limit_backoff_pushes_every_caller_out_and_escalates():
    from marketpulse.extract.ratelimit import BACKOFF_MAX_SECONDS, BACKOFF_START_SECONDS, SharedRateLimiter

    lim = SharedRateLimiter(0.0)
    first = lim.on_rate_limit()
    assert first == BACKOFF_START_SECONDS
    assert lim.on_rate_limit() == first  # the same throttle burst counts once
    assert lim._next_slot > 0  # noqa: SLF001 - every worker's next slot moved out
    lim._pause_until = 0  # noqa: SLF001 - pretend the pause elapsed, then throttled again
    assert lim.on_rate_limit() == 2 * BACKOFF_START_SECONDS
    for _ in range(10):
        lim._pause_until = 0  # noqa: SLF001
        lim.on_rate_limit()
    assert lim._pause == BACKOFF_MAX_SECONDS  # noqa: SLF001 - capped
    lim._pause_until = 0  # noqa: SLF001
    lim.on_success()
    assert lim._pause == 0.0  # noqa: SLF001 - a success resets the escalation
