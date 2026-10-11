"""Daemon: runs scripts/run_daily_update.py at 09:00, 15:00 and 23:00 Israel
time, every day - the same schedule as the CronJob in k8s/eod-update-cronjob.yaml
(`0 9,15,23 * * *`, Asia/Jerusalem), defined once in marketpulse/schedule.py.

Meant to be left running in the background on a single dev machine (see
README.md for nohup/tmux). For production use the k8s CronJob instead of a
long-lived Python daemon.

Each fire runs run_daily_update.py as a subprocess (not imported in-process) so
a crash inside the pipeline can never take the daemon down: it logs the failure
and waits for the next slot. A run that overruns past the next slot simply
skips it (runs never overlap) and the daemon picks up the one after.

Usage: PYTHONPATH=src .venv/bin/python scripts/scheduler.py
"""
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from marketpulse.schedule import describe_schedule, next_scheduled_run  # noqa: E402

PYTHON = sys.executable
RUN_DAILY_UPDATE = ROOT / "scripts" / "run_daily_update.py"
POLL_SECONDS = 20


def run_daily_update() -> None:
    started_at = datetime.now().isoformat(timespec="seconds")
    print(f"[{started_at}] Triggering update ({RUN_DAILY_UPDATE.name})...", flush=True)
    try:
        result = subprocess.run([PYTHON, str(RUN_DAILY_UPDATE)], cwd=ROOT)
    except Exception as exc:  # noqa: BLE001 - a scheduler daemon must never die from a bad run
        print(f"[{datetime.now().isoformat(timespec='seconds')}] Update crashed: {exc}", flush=True)
        return

    finished_at = datetime.now().isoformat(timespec="seconds")
    if result.returncode == 0:
        print(f"[{finished_at}] Update finished successfully.", flush=True)
    else:
        print(f"[{finished_at}] Update FAILED (exit {result.returncode}) - see output above.", flush=True)


def main() -> None:
    print(f"Scheduler started - {describe_schedule()}.", flush=True)
    print("Press Ctrl+C to stop.", flush=True)

    try:
        while True:
            due = next_scheduled_run(datetime.now(timezone.utc))
            print(f"Next run: {due.isoformat(timespec='minutes')}", flush=True)
            while datetime.now(timezone.utc) < due:
                time.sleep(POLL_SECONDS)
            run_daily_update()  # blocks: a slot missed during a long run is skipped, never queued
    except KeyboardInterrupt:
        print("\nScheduler stopped.", flush=True)


if __name__ == "__main__":
    main()
