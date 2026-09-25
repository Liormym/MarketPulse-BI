"""Simple in-session rate limiter for the live "Refresh Data" button.

Session-scoped (Flask's signed cookie session) rather than global, so each
browser session gets its own budget - 5 refreshes per rolling hour, across
all tickers combined. State lives in the session cookie itself (a short
list of timestamps), so it needs no DB table or external store, and it
naturally resets if the Flask process restarts (its secret key is
regenerated on each start - fine for a local, single-operator tool).
"""
import time

REFRESH_LIMIT = 5
REFRESH_WINDOW_SECONDS = 3600
SESSION_KEY = "refresh_history"


def check_and_record_refresh(session) -> tuple[bool, int, int]:
    """Returns (allowed, remaining_after, retry_after_seconds).

    If allowed, this call also records the refresh (so the caller doesn't
    need a separate "record" step). If not allowed, nothing is recorded and
    retry_after_seconds says how long until the oldest refresh ages out.
    """
    now = time.time()
    history = [t for t in session.get(SESSION_KEY, []) if now - t < REFRESH_WINDOW_SECONDS]

    if len(history) >= REFRESH_LIMIT:
        retry_after = int(REFRESH_WINDOW_SECONDS - (now - min(history)))
        session[SESSION_KEY] = history
        return False, 0, max(retry_after, 1)

    history.append(now)
    session[SESSION_KEY] = history
    return True, REFRESH_LIMIT - len(history), 0
