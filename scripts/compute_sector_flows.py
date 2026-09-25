"""Computes FactSectorVolume (20-day avg volume, dollar volume, volume ratio,
Accumulation/Distribution/Neutral flow status) for the 11 S&P 500 Sector
SPDR ETFs, purely from price/volume history already sitting in
FactDailyPrice - no new fetch needed, since the sector ETFs are ordinary
watchlist tickers already covered by scripts/backfill_historical_prices.py.

Usage: PYTHONPATH=src .venv/bin/python scripts/compute_sector_flows.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sqlalchemy import text  # noqa: E402

from marketpulse.load.db import get_engine  # noqa: E402
from marketpulse.sector_flow import SECTOR_SPDR_TICKERS, compute_daily_flows  # noqa: E402


def main() -> None:
    engine = get_engine()
    total_rows = 0

    with engine.begin() as conn:
        for ticker in SECTOR_SPDR_TICKERS:
            asset = conn.execute(
                text('SELECT "AssetKey" FROM "DimAsset" WHERE "Ticker" = :ticker'),
                {"ticker": ticker},
            ).first()
            if asset is None:
                print(f"skip {ticker}: not in DimAsset")
                continue
            asset_key = asset[0]

            rows = conn.execute(
                text(
                    """
                    SELECT d."DateKey", p."Close", p."Volume"
                    FROM "FactDailyPrice" p
                    JOIN "DimDate" d ON d."DateKey" = p."DateKey"
                    WHERE p."AssetKey" = :asset_key
                    ORDER BY d."Date" ASC
                    """
                ),
                {"asset_key": asset_key},
            ).all()
            if not rows:
                print(f"skip {ticker}: no price history")
                continue

            date_keys = [r[0] for r in rows]
            closes = [r[1] for r in rows]
            volumes = [r[2] for r in rows]
            flows = compute_daily_flows(closes, volumes)

            for date_key, volume, flow in zip(date_keys, volumes, flows):
                conn.execute(
                    text(
                        """
                        INSERT INTO "FactSectorVolume"
                            ("AssetKey", "DateKey", "Volume", "AvgVolume20D", "DollarVolume", "VolumeRatio", "FlowStatus")
                        VALUES (:asset_key, :date_key, :volume, :avg_volume, :dollar_volume, :ratio, :status)
                        ON CONFLICT ("AssetKey", "DateKey") DO UPDATE
                            SET "Volume" = EXCLUDED."Volume",
                                "AvgVolume20D" = EXCLUDED."AvgVolume20D",
                                "DollarVolume" = EXCLUDED."DollarVolume",
                                "VolumeRatio" = EXCLUDED."VolumeRatio",
                                "FlowStatus" = EXCLUDED."FlowStatus"
                        """
                    ),
                    {
                        "asset_key": asset_key,
                        "date_key": date_key,
                        "volume": volume,
                        "avg_volume": flow.avg_volume_20d,
                        "dollar_volume": flow.dollar_volume,
                        "ratio": flow.volume_ratio,
                        "status": flow.flow_status,
                    },
                )
            total_rows += len(rows)
            print(f"{ticker}: {len(rows)} rows computed")

    print(f"Total FactSectorVolume rows upserted: {total_rows}")


if __name__ == "__main__":
    main()
