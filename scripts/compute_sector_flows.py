"""Computes FactSectorVolume (20-day avg volume, dollar volume, volume ratio,
Accumulation/Distribution/Neutral flow status) for the 11 S&P 500 Sector
SPDR ETFs, purely from price/volume history already sitting in
FactDailyPrice - no new fetch needed, since the sector ETFs are ordinary
watchlist tickers already covered by scripts/backfill_historical_prices.py.

Then runs the Early Sector Rotation Engine (marketpulse/sector_rotation.py) and
stores its read in FactSectorRotation: each sector's 2-/5-/20-day return
relative to the equal-weight S&P 500 (RSP), its breadth (% of its S&P 1500 stocks that rose)
and the inflow/outflow regime that feeds Pillar 3 of the Investment Score and
the dashboard's Sector Rotation Radar.

Usage: PYTHONPATH=src .venv/bin/python scripts/compute_sector_flows.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sqlalchemy import text  # noqa: E402

from marketpulse.load.db import get_engine  # noqa: E402
from marketpulse.load.freshness import mark_source  # noqa: E402
from marketpulse.sector_flow import SECTOR_SPDR_TICKERS, compute_daily_flows  # noqa: E402
from marketpulse.sector_rotation import REGIME_LABELS, compute_and_store_sector_rotation  # noqa: E402


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

    with engine.begin() as conn:
        rotation = compute_and_store_sector_rotation(conn)
        mark_source(conn, "sector_flows")
    print(f"Total FactSectorVolume rows upserted: {total_rows}")

    print("\nSector rotation (vs equal-weight S&P 500 / RSP), strongest inflow first:")
    for r in sorted(rotation, key=lambda r: r["rotation_score"] if r["rotation_score"] is not None else -9, reverse=True):
        fmt = lambda v, f: "  n/a" if v is None else format(v, f)  # noqa: E731
        print(
            f"  {r['ticker']:<5} 2d {fmt(r['rel_2d_pct'], '+.2f')}pp  5d {fmt(r['rel_5d_pct'], '+.2f')}pp  "
            f"20d {fmt(r['rel_20d_pct'], '+.2f')}pp  breadth {fmt(r['breadth_pct'], '.0f')}% "
            f"({r['advancing']}/{r['declining']})  signal {fmt(r['rotation_score'], '+.2f')}  "
            f"{REGIME_LABELS.get(r['regime'], '-')}"
        )


if __name__ == "__main__":
    main()
