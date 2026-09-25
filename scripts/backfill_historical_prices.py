"""One-time backfill: downloads ~2 years of daily OHLCV history for every
watchlisted ticker via yfinance and idempotently upserts it into
FactDailyPrice (and any missing DimDate rows it needs).

Why: the regular pipeline run only pulls a short trailing window, which
isn't enough history for standard-length technical indicators (RSI-14,
SMA-150/200) feeding the Investment Score. This script is a separate,
one-time top-up — not part of the recurring pipeline.

Safe to re-run: (AssetKey, DateKey) upserts never create duplicates.

Usage: PYTHONPATH=src .venv/bin/python scripts/backfill_historical_prices.py [--days N]
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sqlalchemy import text  # noqa: E402
from sqlalchemy.engine import Connection  # noqa: E402

from marketpulse.extract.prices import fetch_prices  # noqa: E402
from marketpulse.load.db import get_engine  # noqa: E402
from marketpulse.load.upsert import get_asset_key_map, upsert_daily_prices  # noqa: E402


def ensure_dim_dates(conn: Connection, trade_dates) -> int:
    """Upserts a DimDate row for every distinct date in the fetched history.
    2 years back can reach earlier than the rolling window seed_dimensions.py
    normally covers, so this fills any gap rather than assuming it.
    """
    dates = sorted(set(trade_dates))
    for d in dates:
        conn.execute(
            text(
                """
                INSERT INTO "DimDate" ("DateKey", "Date", "IsTradingDay")
                VALUES (:date_key, :date, TRUE)
                ON CONFLICT ("DateKey") DO NOTHING
                """
            ),
            {"date_key": int(d.strftime("%Y%m%d")), "date": d},
        )
    return len(dates)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--days", type=int, default=730, help="Calendar days of history to fetch (default: 730, ~2 years)"
    )
    args = parser.parse_args()

    engine = get_engine()

    with engine.connect() as conn:
        asset_keys = get_asset_key_map(conn)
    tickers = sorted(asset_keys.keys())
    print(f"Fetching {args.days}d of history for {len(tickers)} tickers via yfinance...")

    records, failed = fetch_prices(tickers, lookback_days=args.days)
    print(f"Fetched {len(records)} price rows ({len(failed)} tickers failed)")

    with engine.begin() as conn:
        n_dates = ensure_dim_dates(conn, (r.trade_date for r in records))
        n_upserted = upsert_daily_prices(conn, records, asset_keys)

    print(f"Upserted {n_upserted} FactDailyPrice rows across {n_dates} distinct trading days")
    if failed:
        print(f"Failed tickers (skipped, no history fetched): {', '.join(failed)}")


if __name__ == "__main__":
    main()
