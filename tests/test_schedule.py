from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

from marketpulse.schedule import (
    RUN_TIMES,
    SCHEDULE_TZ_NAME,
    describe_schedule,
    is_overdue,
    last_scheduled_run,
    next_scheduled_run,
    us_market_open,
)

IL = ZoneInfo("Asia/Jerusalem")
ET = ZoneInfo("America/New_York")
ROOT = Path(__file__).resolve().parents[1]


def il(y, m, d, h, mi=0):
    return datetime(y, m, d, h, mi, tzinfo=IL)


def test_three_runs_a_day_every_day():
    assert describe_schedule() == "Daily at 09:00, 15:00, 23:00 Israel time"
    assert next_scheduled_run(il(2026, 10, 11, 8, 0)) == il(2026, 10, 11, 9, 0)
    assert next_scheduled_run(il(2026, 10, 11, 9, 0)) == il(2026, 10, 11, 15, 0)  # strictly after
    assert next_scheduled_run(il(2026, 10, 11, 16, 0)) == il(2026, 10, 11, 23, 0)
    assert next_scheduled_run(il(2026, 10, 11, 23, 30)) == il(2026, 10, 12, 9, 0)
    # weekends are not skipped (news and Bitcoin never stop; Friday's final close lands Saturday 09:00)
    assert next_scheduled_run(il(2026, 10, 9, 23, 30)) == il(2026, 10, 10, 9, 0)
    assert last_scheduled_run(il(2026, 10, 11, 8, 59)) == il(2026, 10, 10, 23, 0)
    assert last_scheduled_run(il(2026, 10, 11, 15, 0)) == il(2026, 10, 11, 15, 0)


def test_works_from_utc_input_and_across_israel_dst_change():
    # Israel leaves DST on 2026-10-25: 23:00 IDT (UTC+3) then 23:00 IST (UTC+2).
    before = datetime(2026, 10, 24, 20, 0, tzinfo=timezone.utc)  # 23:00 IDT
    assert last_scheduled_run(before) == il(2026, 10, 24, 23, 0)
    after = datetime(2026, 10, 25, 21, 0, tzinfo=timezone.utc)  # 23:00 IST
    assert last_scheduled_run(after) == il(2026, 10, 25, 23, 0)
    assert last_scheduled_run(after).utcoffset().total_seconds() == 2 * 3600


def test_overdue_logic():
    now = il(2026, 10, 11, 12, 0)
    assert not is_overdue(il(2026, 10, 11, 9, 20), now)  # the 09:00 run landed
    assert is_overdue(il(2026, 10, 10, 23, 20), now)  # 09:00 run never landed (now past its grace)
    assert is_overdue(None, now)
    # Within the grace window after a scheduled time, last run's data is still fine.
    assert not is_overdue(il(2026, 10, 10, 23, 20), il(2026, 10, 11, 10, 30))
    assert is_overdue(il(2026, 10, 10, 23, 20), il(2026, 10, 11, 11, 1))


def test_market_hours_and_utc_input():
    assert us_market_open(datetime(2026, 10, 9, 10, 0, tzinfo=ET))
    assert not us_market_open(datetime(2026, 10, 9, 16, 0, tzinfo=ET))
    assert not us_market_open(datetime(2026, 10, 10, 11, 0, tzinfo=ET))  # Saturday
    assert us_market_open(datetime(2026, 10, 9, 14, 0, tzinfo=timezone.utc))  # 10:00 ET


def test_k8s_cronjob_matches_the_schedule_the_ui_advertises():
    cj = yaml.safe_load((ROOT / "k8s" / "eod-update-cronjob.yaml").read_text())
    minute, hours, dom, month, dow = cj["spec"]["schedule"].split()
    assert cj["spec"]["timeZone"] == SCHEDULE_TZ_NAME
    assert minute == "0" and (dom, month, dow) == ("*", "*", "*")
    assert [int(h) for h in hours.split(",")] == [t.hour for t in RUN_TIMES]
