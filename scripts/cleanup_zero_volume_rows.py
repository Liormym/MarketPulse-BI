"""One-time DB fix: deletes existing Volume=0 rows from FactDailyPrice (see
src/marketpulse/extract/prices.py, which now filters these out at the
source going forward - this script cleans up rows ingested before that fix
landed) and recomputes FactStockTechnicals for every affected ticker, since
removing rows changes the rolling-window inputs for SMA/ATR/AvgVolume.

Confirmed cause: yfinance returns a phantom row (stale carried-forward
Close, zero volume) for exchange-specific non-trading days DimDate's Mon-Fri
IsTradingDay logic doesn't account for - e.g. the Tel Aviv Stock Exchange's
Friday/Saturday weekend. Most of the 36k+ affected rows are old US-listed
tickers' 1970s-2010s history (a yfinance data-quality gap, harmless to
current scoring since only the trailing ~200 days matter there) but 4 TASE
tickers (BEZQ.TA, GNRS.TA, ORL.TA, VRDS.TA) had *recent, recurring* phantom
rows measurably distorting their live AvgVolume20D/ATR14.

Safe to re-run: deleting rows that no longer exist is a no-op, and
technicals recompute is idempotent.

Usage: PYTHONPATH=src .venv/bin/python scripts/cleanup_zero_volume_rows.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sqlalchemy import text  # noqa: E402

from marketpulse.load.db import get_engine  # noqa: E402
from marketpulse.sector_flow import SECTOR_SPDR_TICKERS  # noqa: E402
from marketpulse.technicals import compute_and_upsert_technicals_for_asset  # noqa: E402


def main() -> None:
    engine = get_engine()

    with engine.begin() as conn:
        affected = conn.execute(
            text(
                """
                SELECT DISTINCT a."AssetKey", a."Ticker"
                FROM "FactDailyPrice" p
                JOIN "DimAsset" a ON a."AssetKey" = p."AssetKey"
                WHERE p."Volume" = 0
                """
            )
        ).all()
        print(f"{len(affected)} tickers have zero-volume rows: {[t for _, t in affected]}")

        result = conn.execute(text('DELETE FROM "FactDailyPrice" WHERE "Volume" = 0'))
        print(f"Deleted {result.rowcount} zero-volume rows")

    total_tech_rows = 0
    with engine.begin() as conn:
        for asset_key, ticker in affected:
            n = compute_and_upsert_technicals_for_asset(conn, asset_key)
            total_tech_rows += n
            print(f"  recomputed technicals for {ticker}: {n} rows")

    print(f"Recomputed {total_tech_rows} FactStockTechnicals rows across {len(affected)} tickers")

    affected_tickers = {t for _, t in affected}
    if affected_tickers & set(SECTOR_SPDR_TICKERS):
        print("A sector SPDR was affected - re-run scripts/compute_sector_flows.py to refresh FactSectorVolume too")
    else:
        print("No sector SPDR was affected - FactSectorVolume unchanged")


if __name__ == "__main__":
    main()
