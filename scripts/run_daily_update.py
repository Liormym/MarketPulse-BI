"""Master End-of-Day (EOD) orchestrator: runs the full daily data-refresh
pipeline in strict dependency order, so nothing downstream ever computes
on data an upstream step hasn't actually produced. This is the exact bug
class that produced two different "as of" dates on the dashboard: sector
flows were recomputed on data that itself had gone stale, and nothing ever
re-ran the price fetch, technicals, sector flows, and scores TOGETHER, in
the right order, on a schedule.

Step order (each step's output is the next step's input):
  1. fetch_macro_indicators.py    - 10Y/2Y/oil/BTC/KOSPI/S&P500/NASDAQ/RSP
  2. marketpulse.pipeline         - fresh prices (+ news + FinBERT
                                     sentiment) for every watchlist ticker,
                                     trailing window (see that module's
                                     docstring - NOT the one-time full
                                     historical backfill)
  3. compute_stock_technicals.py  - SMA/ATR/gap, recomputed from the
                                     prices step 2 just wrote
  4. compute_sector_flows.py      - Accumulation/Distribution/Neutral +
                                     volume ratio, from the prices step 2
                                     just wrote
  5. compute_investment_scores.py - Investment Score for every ticker,
                                     from steps 2-4's output (technicals,
                                     sector flow, price/volume)

Each step runs as its own subprocess (matching how it's documented to be
run standalone) and the pipeline STOPS on the first failing step rather
than continuing with stale/partial upstream data - a partial run left
half-updated is worse than no run at all in a system whose whole premise
is explainable, trustworthy numbers. Exits non-zero on failure so
scheduler.py or cron can detect and alert on it.

Usage: PYTHONPATH=src .venv/bin/python scripts/run_daily_update.py
"""
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PYTHON = sys.executable

STEPS = [
    ("Fetch macro indicators", [PYTHON, str(ROOT / "scripts" / "fetch_macro_indicators.py")]),
    ("Fetch fresh prices/news/sentiment", [PYTHON, "-m", "marketpulse.pipeline"]),
    ("Recompute technicals (SMA/ATR/gap)", [PYTHON, str(ROOT / "scripts" / "compute_stock_technicals.py")]),
    ("Compute sector flows", [PYTHON, str(ROOT / "scripts" / "compute_sector_flows.py")]),
    ("Compute investment scores", [PYTHON, str(ROOT / "scripts" / "compute_investment_scores.py")]),
]


def main() -> None:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT / "src")

    started = time.monotonic()
    print(f"=== EOD update starting: {len(STEPS)} steps ===", flush=True)

    for i, (label, cmd) in enumerate(STEPS, start=1):
        step_started = time.monotonic()
        print(f"\n--- Step {i}/{len(STEPS)}: {label} ---", flush=True)
        result = subprocess.run(cmd, cwd=ROOT, env=env)
        elapsed = time.monotonic() - step_started

        if result.returncode != 0:
            print(
                f"\n!!! Step {i} ({label}) failed after {elapsed:.1f}s (exit {result.returncode}) - "
                f"stopping pipeline, NOT running downstream steps on stale/partial data.",
                flush=True,
            )
            sys.exit(result.returncode)

        print(f"--- Step {i} done in {elapsed:.1f}s ---", flush=True)

    total = time.monotonic() - started
    print(f"\n=== EOD update finished successfully in {total:.1f}s ===", flush=True)


if __name__ == "__main__":
    main()
