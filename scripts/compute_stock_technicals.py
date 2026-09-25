"""Computes FactStockTechnicals (SMA-20/50/150/200, ATR-14, 20-day avg
volume, daily gap %) for every ticker in the watchlist, purely from
FactDailyPrice history already in the DB - no new fetch needed.

Usage: PYTHONPATH=src .venv/bin/python scripts/compute_stock_technicals.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sqlalchemy import text  # noqa: E402

from marketpulse.load.db import get_engine  # noqa: E402
from marketpulse.technicals import compute_and_upsert_technicals_for_asset  # noqa: E402


def main() -> None:
    engine = get_engine()
    total_rows = 0
    skipped = []

    with engine.begin() as conn:
        assets = conn.execute(text('SELECT "AssetKey", "Ticker" FROM "DimAsset"')).all()

        for asset_key, ticker in assets:
            n = compute_and_upsert_technicals_for_asset(conn, asset_key)
            if n == 0:
                skipped.append(ticker)
            total_rows += n

    print(f"Computed technicals for {len(assets) - len(skipped)} tickers, {total_rows} rows upserted")
    if skipped:
        print(f"Skipped (no price history): {', '.join(skipped)}")


if __name__ == "__main__":
    main()
