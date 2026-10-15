"""Seeds DimDate (a rolling calendar window) and DimAsset (from config/watchlist.yaml).

Safe to re-run: uses upserts, never creates duplicates.
Usage: python scripts/seed_dimensions.py [--years-back N] [--years-forward N]
"""
import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

import yaml
from sqlalchemy import create_engine, text

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from marketpulse.config import settings  # noqa: E402


def seed_dates(engine, years_back: int, years_forward: int) -> int:
    start = date.today().replace(month=1, day=1) - timedelta(days=365 * years_back)
    end = date.today().replace(month=12, day=31) + timedelta(days=365 * years_forward)

    rows = []
    d = start
    while d <= end:
        is_trading_day = d.weekday() < 5  # Mon-Fri; holidays are out of scope for the prototype
        rows.append({"date_key": int(d.strftime("%Y%m%d")), "date": d, "is_trading": is_trading_day})
        d += timedelta(days=1)

    with engine.begin() as conn:
        for r in rows:
            conn.execute(
                text(
                    """
                    INSERT INTO "DimDate" ("DateKey", "Date", "IsTradingDay")
                    VALUES (:date_key, :date, :is_trading)
                    ON CONFLICT ("DateKey") DO UPDATE SET "IsTradingDay" = EXCLUDED."IsTradingDay"
                    """
                ),
                r,
            )
    return len(rows)


def seed_assets(engine) -> int:
    watchlist = yaml.safe_load(settings.watchlist_abs_path.read_text())
    assets = watchlist["assets"]

    with engine.begin() as conn:
        old_divisors = dict(conn.execute(text('SELECT "Ticker", "PriceDivisor" FROM "DimAsset"')).all())
        for a in assets:
            conn.execute(
                text(
                    """
                    INSERT INTO "DimAsset" ("Ticker", "CompanyName", "Sector", "Universe", "Exchange", "Currency", "PriceDivisor")
                    VALUES (:ticker, :company_name, :sector, :universe, :exchange, :currency, :price_divisor)
                    ON CONFLICT ("Ticker") DO UPDATE
                        SET "CompanyName" = EXCLUDED."CompanyName",
                            "Sector" = EXCLUDED."Sector",
                            "Universe" = EXCLUDED."Universe",
                            "Exchange" = EXCLUDED."Exchange",
                            "Currency" = EXCLUDED."Currency",
                            "PriceDivisor" = EXCLUDED."PriceDivisor"
                    """
                ),
                {
                    "ticker": a["ticker"],
                    "company_name": a["company_name"],
                    "sector": a.get("sector"),
                    "universe": a.get("universe", "sp500"),
                    "exchange": a.get("exchange"),
                    "currency": a.get("currency", "USD"),
                    "price_divisor": int(a.get("price_divisor", 1)),
                },
            )
        rescaled = [
            a["ticker"]
            for a in assets
            if int(a.get("price_divisor", 1)) == 100 and old_divisors.get(a["ticker"], 1) == 1
        ]
        touched = [t for t in rescaled if rescale_stored_prices(conn, t, 100) > 0]
    if touched:
        print(f"rescaled already-stored prices of {len(touched)} agorot-quoted ticker(s): {', '.join(touched)}")
    return len(assets)


def rescale_stored_prices(conn, ticker: str, divisor: int) -> int:
    """Rows stored BEFORE an asset was recognised as agorot-quoted hold agorot. Divides
    OHLC once (it only runs when PriceDivisor changes 1 -> 100, so it cannot double-apply)
    and recomputes the asset's technicals, which were derived from the unscaled prices."""
    from marketpulse.technicals import compute_and_upsert_technicals_for_asset

    key = conn.execute(text('SELECT "AssetKey" FROM "DimAsset" WHERE "Ticker" = :t'), {"t": ticker}).scalar()
    n = conn.execute(
        text(
            """
            UPDATE "FactDailyPrice"
            SET "Close" = "Close" / :d, "Open" = "Open" / :d, "High" = "High" / :d, "Low" = "Low" / :d
            WHERE "AssetKey" = :k
            """
        ),
        {"d": divisor, "k": key},
    ).rowcount
    conn.execute(text('DELETE FROM "FactStockTechnicals" WHERE "AssetKey" = :k'), {"k": key})
    compute_and_upsert_technicals_for_asset(conn, key)
    return n


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--years-back", type=int, default=1)
    parser.add_argument("--years-forward", type=int, default=1)
    args = parser.parse_args()

    engine = create_engine(settings.database_url)
    n_dates = seed_dates(engine, args.years_back, args.years_forward)
    n_assets = seed_assets(engine)
    print(f"seeded {n_dates} DimDate rows, {n_assets} DimAsset rows")


if __name__ == "__main__":
    main()
