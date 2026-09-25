import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "webapp"))

from rate_limit import REFRESH_LIMIT, REFRESH_WINDOW_SECONDS, check_and_record_refresh  # noqa: E402


def test_allows_up_to_the_limit():
    session = {}
    for i in range(REFRESH_LIMIT):
        allowed, remaining, retry_after = check_and_record_refresh(session)
        assert allowed is True
        assert remaining == REFRESH_LIMIT - (i + 1)
        assert retry_after == 0


def test_blocks_the_next_one_after_the_limit():
    session = {}
    for _ in range(REFRESH_LIMIT):
        check_and_record_refresh(session)

    allowed, remaining, retry_after = check_and_record_refresh(session)
    assert allowed is False
    assert remaining == 0
    assert 0 < retry_after <= REFRESH_WINDOW_SECONDS


def test_old_refreshes_outside_the_window_do_not_count():
    session = {"refresh_history": [0.0] * REFRESH_LIMIT}  # ancient timestamps, well outside the window
    allowed, remaining, retry_after = check_and_record_refresh(session)
    assert allowed is True
    assert remaining == REFRESH_LIMIT - 1


def test_sessions_are_independent():
    session_a = {}
    session_b = {}
    for _ in range(REFRESH_LIMIT):
        check_and_record_refresh(session_a)

    allowed_a, _, _ = check_and_record_refresh(session_a)
    allowed_b, _, _ = check_and_record_refresh(session_b)
    assert allowed_a is False
    assert allowed_b is True
