"""Computes FactStockTechnicals (SMA-20/50/150/200, ATR-14 + its 90-day stats,
RSI-14, 20-day avg volume, daily gap %) for every ticker in the watchlist,
purely from FactDailyPrice history already in the DB - no new fetch needed.

INCREMENTAL by default: a stock that already has technicals only gets its
newest bars (plus a few already-computed ones, to pick up revised closes)
recomputed, using 250 earlier bars as look-back context. A stock with no
technicals yet (e.g. a newly added universe) gets one full baseline pass over
its whole history. See marketpulse/technicals.py.

Usage:
  PYTHONPATH=src .venv/bin/python scripts/compute_stock_technicals.py          # daily: incremental + baseline for new stocks
  PYTHONPATH=src .venv/bin/python scripts/compute_stock_technicals.py --full   # recompute everything (after a formula change)
"""
import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sqlalchemy import text  # noqa: E402

from marketpulse.load.db import get_engine  # noqa: E402
from marketpulse.technicals import compute_and_upsert_technicals  # noqa: E402

COMMIT_EVERY = 100  # assets per transaction: bounded memory, progress survives a crash


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--full", action="store_true", help="recompute every bar of every asset instead of incrementally")
    args = parser.parse_args()

    engine = get_engine()
    started = time.monotonic()
    rows_by_mode = {"baseline": 0, "incremental": 0}
    assets_by_mode = {"baseline": 0, "incremental": 0}
    skipped = []

    with engine.connect() as conn:
        assets = conn.execute(text('SELECT "AssetKey", "Ticker" FROM "DimAsset" ORDER BY "AssetKey"')).all()

    for start in range(0, len(assets), COMMIT_EVERY):
        with engine.begin() as conn:
            for asset_key, ticker in assets[start : start + COMMIT_EVERY]:
                n, mode = compute_and_upsert_technicals(conn, asset_key, full=args.full)
                if mode == "none":
                    skipped.append(ticker)
                    continue
                rows_by_mode[mode] += n
                assets_by_mode[mode] += 1
        done = min(start + COMMIT_EVERY, len(assets))
        print(f"  {done}/{len(assets)} assets ({time.monotonic() - started:.0f}s)", flush=True)

    elapsed = time.monotonic() - started
    print(
        f"Technicals: {assets_by_mode['baseline']} assets baselined ({rows_by_mode['baseline']} rows), "
        f"{assets_by_mode['incremental']} incremental ({rows_by_mode['incremental']} rows) in {elapsed:.1f}s"
    )
    if skipped:
        print(f"Skipped (no price history): {', '.join(skipped)}")


if __name__ == "__main__":
    main()
