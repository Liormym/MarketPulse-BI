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
from marketpulse.technicals import DailyBar, compute_technicals  # noqa: E402


def main() -> None:
    engine = get_engine()
    total_rows = 0
    skipped = []

    with engine.begin() as conn:
        assets = conn.execute(text('SELECT "AssetKey", "Ticker" FROM "DimAsset"')).all()

        for asset_key, ticker in assets:
            rows = conn.execute(
                text(
                    """
                    SELECT d."DateKey", p."Open", p."High", p."Low", p."Close", p."Volume"
                    FROM "FactDailyPrice" p
                    JOIN "DimDate" d ON d."DateKey" = p."DateKey"
                    WHERE p."AssetKey" = :asset_key
                    ORDER BY d."Date" ASC
                    """
                ),
                {"asset_key": asset_key},
            ).all()
            if not rows:
                skipped.append(ticker)
                continue

            date_keys = [r[0] for r in rows]
            bars = [DailyBar(open=r[1], high=r[2], low=r[3], close=r[4], volume=r[5]) for r in rows]
            technicals = compute_technicals(bars)

            for date_key, t in zip(date_keys, technicals):
                conn.execute(
                    text(
                        """
                        INSERT INTO "FactStockTechnicals"
                            ("AssetKey", "DateKey", "SMA20", "SMA50", "SMA150", "SMA200",
                             "ATR14", "AvgVolume20D", "GapPct")
                        VALUES (:asset_key, :date_key, :sma20, :sma50, :sma150, :sma200,
                                :atr14, :avg_volume, :gap_pct)
                        ON CONFLICT ("AssetKey", "DateKey") DO UPDATE
                            SET "SMA20" = EXCLUDED."SMA20", "SMA50" = EXCLUDED."SMA50",
                                "SMA150" = EXCLUDED."SMA150", "SMA200" = EXCLUDED."SMA200",
                                "ATR14" = EXCLUDED."ATR14", "AvgVolume20D" = EXCLUDED."AvgVolume20D",
                                "GapPct" = EXCLUDED."GapPct"
                        """
                    ),
                    {
                        "asset_key": asset_key,
                        "date_key": date_key,
                        "sma20": t.sma20,
                        "sma50": t.sma50,
                        "sma150": t.sma150,
                        "sma200": t.sma200,
                        "atr14": t.atr14,
                        "avg_volume": t.avg_volume_20d,
                        "gap_pct": t.gap_pct,
                    },
                )
            total_rows += len(rows)

    print(f"Computed technicals for {len(assets) - len(skipped)} tickers, {total_rows} rows upserted")
    if skipped:
        print(f"Skipped (no price history): {', '.join(skipped)}")


if __name__ == "__main__":
    main()
