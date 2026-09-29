"""Daemon: triggers scripts/run_daily_update.py at 23:10 on every US market
weekday (Mon-Fri), the equivalent of the cron expression `10 23 * * 1-5` -
30 minutes after the pipeline CronJob schedule already documented in
k8s/pipeline-cronjob.yaml, giving the market close time to settle before
the EOD update runs. This process is meant to be left running in the
background (see README.md for nohup/tmux instructions); for a real
production deployment, prefer the equivalent crontab entry or the existing
k8s/ CronJob pattern instead of a long-lived Python daemon - this script is
the simple, dependency-light option for a single dev machine.

Each fire runs run_daily_update.py as a subprocess (not imported in-process)
so a crash inside that pipeline can never take the scheduler daemon down
with it - the daemon logs the failure and keeps waiting for tomorrow's run.

Usage: PYTHONPATH=src .venv/bin/python scripts/scheduler.py
"""
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import schedule

ROOT = Path(__file__).resolve().parents[1]
PYTHON = sys.executable
RUN_DAILY_UPDATE = ROOT / "scripts" / "run_daily_update.py"
DAILY_UPDATE_TIME = "23:10"


def run_daily_update() -> None:
    started_at = datetime.now().isoformat(timespec="seconds")
    print(f"[{started_at}] Triggering EOD update ({RUN_DAILY_UPDATE.name})...", flush=True)
    try:
        result = subprocess.run([PYTHON, str(RUN_DAILY_UPDATE)], cwd=ROOT)
    except Exception as exc:  # noqa: BLE001 - a scheduler daemon must never die from a bad run
        print(f"[{datetime.now().isoformat(timespec='seconds')}] EOD update crashed: {exc}", flush=True)
        return

    finished_at = datetime.now().isoformat(timespec="seconds")
    if result.returncode == 0:
        print(f"[{finished_at}] EOD update finished successfully.", flush=True)
    else:
        print(f"[{finished_at}] EOD update FAILED (exit {result.returncode}) - see output above.", flush=True)


def main() -> None:
    # Explicit Mon-Fri registration, not schedule.every().day, to match the
    # crontab expression `10 23 * * 1-5` (US market weekdays) exactly -
    # there's nothing to update on a day the market didn't trade.
    schedule.every().monday.at(DAILY_UPDATE_TIME).do(run_daily_update)
    schedule.every().tuesday.at(DAILY_UPDATE_TIME).do(run_daily_update)
    schedule.every().wednesday.at(DAILY_UPDATE_TIME).do(run_daily_update)
    schedule.every().thursday.at(DAILY_UPDATE_TIME).do(run_daily_update)
    schedule.every().friday.at(DAILY_UPDATE_TIME).do(run_daily_update)

    print(f"Scheduler started - EOD update will run at {DAILY_UPDATE_TIME} on US market weekdays.", flush=True)
    print("Press Ctrl+C to stop.", flush=True)

    try:
        while True:
            schedule.run_pending()
            time.sleep(30)
    except KeyboardInterrupt:
        print("\nScheduler stopped.", flush=True)


if __name__ == "__main__":
    main()
