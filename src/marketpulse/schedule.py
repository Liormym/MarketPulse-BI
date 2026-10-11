"""When the data-refresh runs are scheduled, and whether data is overdue.

The full update (scripts/run_daily_update.py) runs three times a day, EVERY day,
at 09:00, 15:00 and 23:00 Israel time. This module is the single definition
used by the UI, the dev scheduler (scripts/scheduler.py) and the
k8s/eod-update-cronjob.yaml consistency test.

In New York time these are roughly 02:00 (after the close), 08:00 (before the
open) and 16:00 (the closing bell; the daily bar may still be provisional, the
09:00 run then writes the final one). The offset moves by an hour for a few
weeks a year because Israel and the US change clocks on different dates; the
Israel-time schedule itself never moves.
"""
from __future__ import annotations

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

SCHEDULE_TZ = ZoneInfo("Asia/Jerusalem")
SCHEDULE_TZ_NAME = "Asia/Jerusalem"
RUN_TIMES = (time(9, 0), time(15, 0), time(23, 0))
RUN_GRACE = timedelta(hours=2)  # a full run takes ~25-45 min; headroom before data is called overdue

EASTERN = ZoneInfo("America/New_York")
MARKET_OPEN, MARKET_CLOSE = time(9, 30), time(16, 0)


def describe_schedule() -> str:
    return "Daily at " + ", ".join(t.strftime("%H:%M") for t in RUN_TIMES) + " Israel time"


def _runs_around(local: datetime) -> list[datetime]:
    day = local.date()
    return [
        datetime.combine(day + timedelta(days=offset), t, tzinfo=SCHEDULE_TZ)
        for offset in (-1, 0, 1)
        for t in RUN_TIMES
    ]


def last_scheduled_run(now: datetime) -> datetime:
    """Most recent scheduled run time at or before `now`."""
    local = now.astimezone(SCHEDULE_TZ)
    return max(r for r in _runs_around(local) if r <= local)


def next_scheduled_run(now: datetime) -> datetime:
    """First scheduled run strictly after `now`."""
    local = now.astimezone(SCHEDULE_TZ)
    return min(r for r in _runs_around(local) if r > local)


def is_overdue(refreshed_at: datetime | None, now: datetime) -> bool:
    """True when a scheduled run should have completed since `refreshed_at`
    (or the data has never been recorded as refreshed)."""
    if refreshed_at is None:
        return True
    last = last_scheduled_run(now)
    return now > last + RUN_GRACE and refreshed_at < last


def us_market_open(now: datetime) -> bool:
    """Regular session Mon-Fri 09:30-16:00 ET (holidays not modelled)."""
    local = now.astimezone(EASTERN)
    return local.weekday() < 5 and MARKET_OPEN <= local.time() < MARKET_CLOSE
