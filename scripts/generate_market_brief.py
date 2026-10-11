"""Generates and stores the rule-based Daily Macro Brief (DailyMarketBrief).

Runs as the last step of the EOD update (scripts/run_daily_update.py), after
macro indicators, prices and technicals are fresh. Idempotent: re-running on
the same market date overwrites that day's row.

Usage: PYTHONPATH=src .venv/bin/python scripts/generate_market_brief.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from marketpulse.load.db import get_engine  # noqa: E402
from marketpulse.load.freshness import mark_source  # noqa: E402
from marketpulse.market_brief import generate_and_store  # noqa: E402


def main() -> None:
    with get_engine().begin() as conn:
        result = generate_and_store(conn)
        if result is not None:
            mark_source(conn, "brief")
    if result is None:
        sys.exit("no macro data to summarise")
    brief, facts = result
    print(f"market date {facts['market_date']}: {brief}")


if __name__ == "__main__":
    main()
