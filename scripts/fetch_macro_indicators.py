"""Backfills MacroIndicators: US 10Y/2Y Treasury yields (FRED), WTI crude oil,
Bitcoin (global liquidity proxy), and the KOSPI index (Asian manufacturing/
tech flow proxy) - all via yfinance except the yields. MarketBreadth/
AAIISentiment are left NULL - placeholders per spec, not programmatically
fetchable from a free source.

FRED's no-auth CSV endpoint (no API key needed) is used for yields rather
than a yfinance proxy: ^IRX (often mistaken for a 2Y yield) is actually the
13-week T-bill, and there's no reliable yfinance ticker for the 2Y
constant-maturity yield. DGS10/DGS2 give both yields from the same
authoritative source.

Note Bitcoin trades 24/7 (no weekend gaps, unlike every other series here)
and KOSPI follows the KST trading calendar, not NYSE's - both are merged
into the same DateKey-keyed table as everything else regardless.

Usage: PYTHONPATH=src .venv/bin/python scripts/fetch_macro_indicators.py [--days N]
"""
import argparse
import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pandas as pd  # noqa: E402
import requests  # noqa: E402
import yfinance as yf  # noqa: E402
from sqlalchemy import text  # noqa: E402

from marketpulse.load.db import get_engine  # noqa: E402

FRED_CSV_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}"


def fetch_fred_series(series_id: str) -> pd.Series:
    resp = requests.get(FRED_CSV_URL.format(series_id=series_id), timeout=15)
    resp.raise_for_status()
    df = pd.read_csv(io.StringIO(resp.text), parse_dates=["observation_date"])
    df = df.set_index("observation_date")[series_id]
    return pd.to_numeric(df, errors="coerce").dropna()


def fetch_oil_prices(days: int) -> pd.Series:
    hist = yf.Ticker("CL=F").history(period=f"{days}d", interval="1d")
    return hist["Close"]


def fetch_bitcoin_prices(days: int) -> pd.Series:
    hist = yf.Ticker("BTC-USD").history(period=f"{days}d", interval="1d")
    return hist["Close"]


def fetch_kospi_index(days: int) -> pd.Series:
    hist = yf.Ticker("^KS11").history(period=f"{days}d", interval="1d")
    return hist["Close"]


def ensure_dim_dates(conn, dates) -> int:
    dates = sorted(set(dates))
    for d in dates:
        conn.execute(
            text(
                """
                INSERT INTO "DimDate" ("DateKey", "Date", "IsTradingDay")
                VALUES (:date_key, :date, TRUE)
                ON CONFLICT ("DateKey") DO NOTHING
                """
            ),
            {"date_key": int(d.strftime("%Y%m%d")), "date": d.date()},
        )
    return len(dates)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=730, help="Calendar days of history (default: 730)")
    args = parser.parse_args()

    print("Fetching DGS10/DGS2 from FRED and CL=F/BTC-USD/^KS11 from yfinance...")
    ten_year = fetch_fred_series("DGS10")
    two_year = fetch_fred_series("DGS2")
    oil = fetch_oil_prices(args.days)
    bitcoin = fetch_bitcoin_prices(args.days)
    kospi = fetch_kospi_index(args.days)

    cutoff = pd.Timestamp.today() - pd.Timedelta(days=args.days)
    ten_year = ten_year[ten_year.index >= cutoff]
    two_year = two_year[two_year.index >= cutoff]

    oil_by_date = {ts.tz_localize(None): float(v) for ts, v in oil.items()}
    bitcoin_by_date = {ts.tz_localize(None): float(v) for ts, v in bitcoin.items()}
    kospi_by_date = {ts.tz_localize(None): float(v) for ts, v in kospi.items()}

    all_dates = sorted(
        set(ten_year.index)
        | set(two_year.index)
        | set(oil_by_date)
        | set(bitcoin_by_date)
        | set(kospi_by_date)
    )

    engine = get_engine()
    with engine.begin() as conn:
        ensure_dim_dates(conn, all_dates)
        n = 0
        for d in all_dates:
            date_key = int(d.strftime("%Y%m%d"))
            conn.execute(
                text(
                    """
                    INSERT INTO "MacroIndicators"
                        ("DateKey", "TenYearYield", "TwoYearYield", "CrudeOilPrice", "BitcoinPrice", "KospiIndex")
                    VALUES (:date_key, :ten_year, :two_year, :oil, :bitcoin, :kospi)
                    ON CONFLICT ("DateKey") DO UPDATE
                        SET "TenYearYield" = COALESCE(EXCLUDED."TenYearYield", "MacroIndicators"."TenYearYield"),
                            "TwoYearYield" = COALESCE(EXCLUDED."TwoYearYield", "MacroIndicators"."TwoYearYield"),
                            "CrudeOilPrice" = COALESCE(EXCLUDED."CrudeOilPrice", "MacroIndicators"."CrudeOilPrice"),
                            "BitcoinPrice" = COALESCE(EXCLUDED."BitcoinPrice", "MacroIndicators"."BitcoinPrice"),
                            "KospiIndex" = COALESCE(EXCLUDED."KospiIndex", "MacroIndicators"."KospiIndex")
                    """
                ),
                {
                    "date_key": date_key,
                    "ten_year": float(ten_year.get(d)) if d in ten_year.index else None,
                    "two_year": float(two_year.get(d)) if d in two_year.index else None,
                    "oil": oil_by_date.get(d),
                    "bitcoin": bitcoin_by_date.get(d),
                    "kospi": kospi_by_date.get(d),
                },
            )
            n += 1
    print(f"Upserted {n} MacroIndicators rows ({all_dates[0].date()} to {all_dates[-1].date()})")


if __name__ == "__main__":
    main()
