"""Backfills MacroIndicators: US 10Y/2Y Treasury yields (FRED), WTI crude oil,
Bitcoin (global liquidity proxy), the KOSPI index (Asian manufacturing/tech
flow proxy), and three major US equity indices - S&P 500, NASDAQ Composite,
and the S&P 500 Equal Weight ETF (RSP) - all via yfinance except the
yields. RSP vs. SPX is a classic breadth tell: when RSP lags the cap-
weighted SPX, gains are concentrated in a handful of mega-caps rather than
broad-based. MarketBreadth/AAIISentiment are left NULL - placeholders per
spec, not programmatically fetchable from a free source.

FRED's no-auth CSV endpoint (no API key needed) is used for yields rather
than a yfinance proxy: ^IRX (often mistaken for a 2Y yield) is actually the
13-week T-bill, and there's no reliable yfinance ticker for the 2Y
constant-maturity yield. DGS10/DGS2 give both yields from the same
authoritative source.

Note Bitcoin trades 24/7 (no weekend gaps, unlike every other series here)
and KOSPI follows the KST trading calendar, not NYSE's - both are merged
into the same DateKey-keyed table as everything else regardless.

Also computes and stores each series' own 1-day % change (pandas
.pct_change(), i.e. relative to that series' own previous reading - not a
fixed calendar day, since yields/oil/KOSPI/Bitcoin don't share one trading
calendar). Computed on the raw fetched series BEFORE the --days cutoff is
applied, so the first row inside the window still gets a real change
instead of an artificial null.

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


def fetch_sp500_index(days: int) -> pd.Series:
    hist = yf.Ticker("^GSPC").history(period=f"{days}d", interval="1d")
    return hist["Close"]


def fetch_nasdaq_index(days: int) -> pd.Series:
    hist = yf.Ticker("^IXIC").history(period=f"{days}d", interval="1d")
    return hist["Close"]


def fetch_rsp_price(days: int) -> pd.Series:
    hist = yf.Ticker("RSP").history(period=f"{days}d", interval="1d")
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

    print("Fetching DGS10/DGS2 from FRED and CL=F/BTC-USD/^KS11/^GSPC/^IXIC/RSP from yfinance...")
    ten_year = fetch_fred_series("DGS10")
    two_year = fetch_fred_series("DGS2")
    oil = fetch_oil_prices(args.days)
    bitcoin = fetch_bitcoin_prices(args.days)
    kospi = fetch_kospi_index(args.days)
    sp500 = fetch_sp500_index(args.days)
    nasdaq = fetch_nasdaq_index(args.days)
    rsp = fetch_rsp_price(args.days)

    # Day-over-day % change vs. each series' own previous reading, computed
    # BEFORE the cutoff filter below so the window's first row still has a
    # real change rather than an artificial null.
    ten_year_chg = ten_year.pct_change() * 100
    two_year_chg = two_year.pct_change() * 100
    oil_chg = oil.pct_change() * 100
    bitcoin_chg = bitcoin.pct_change() * 100
    kospi_chg = kospi.pct_change() * 100
    sp500_chg = sp500.pct_change() * 100
    nasdaq_chg = nasdaq.pct_change() * 100
    rsp_chg = rsp.pct_change() * 100

    cutoff = pd.Timestamp.today() - pd.Timedelta(days=args.days)
    ten_year = ten_year[ten_year.index >= cutoff]
    two_year = two_year[two_year.index >= cutoff]
    ten_year_chg = ten_year_chg[ten_year_chg.index >= cutoff]
    two_year_chg = two_year_chg[two_year_chg.index >= cutoff]

    def _naive_dict(series: pd.Series) -> dict:
        return {ts.tz_localize(None): float(v) for ts, v in series.items() if pd.notna(v)}

    oil_by_date = _naive_dict(oil)
    bitcoin_by_date = _naive_dict(bitcoin)
    kospi_by_date = _naive_dict(kospi)
    sp500_by_date = _naive_dict(sp500)
    nasdaq_by_date = _naive_dict(nasdaq)
    rsp_by_date = _naive_dict(rsp)
    oil_chg_by_date = _naive_dict(oil_chg)
    bitcoin_chg_by_date = _naive_dict(bitcoin_chg)
    kospi_chg_by_date = _naive_dict(kospi_chg)
    sp500_chg_by_date = _naive_dict(sp500_chg)
    nasdaq_chg_by_date = _naive_dict(nasdaq_chg)
    rsp_chg_by_date = _naive_dict(rsp_chg)

    ten_year_chg_by_date = {ts: float(v) for ts, v in ten_year_chg.items() if pd.notna(v)}
    two_year_chg_by_date = {ts: float(v) for ts, v in two_year_chg.items() if pd.notna(v)}

    all_dates = sorted(
        set(ten_year.index)
        | set(two_year.index)
        | set(oil_by_date)
        | set(bitcoin_by_date)
        | set(kospi_by_date)
        | set(sp500_by_date)
        | set(nasdaq_by_date)
        | set(rsp_by_date)
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
                        ("DateKey", "TenYearYield", "TwoYearYield", "CrudeOilPrice", "BitcoinPrice", "KospiIndex",
                         "SP500Index", "NasdaqIndex", "RSPPrice",
                         "TenYearYieldChangePct", "TwoYearYieldChangePct", "CrudeOilChangePct",
                         "BitcoinChangePct", "KospiChangePct", "SP500ChangePct", "NasdaqChangePct", "RSPChangePct")
                    VALUES (:date_key, :ten_year, :two_year, :oil, :bitcoin, :kospi,
                            :sp500, :nasdaq, :rsp,
                            :ten_year_chg, :two_year_chg, :oil_chg, :bitcoin_chg, :kospi_chg,
                            :sp500_chg, :nasdaq_chg, :rsp_chg)
                    ON CONFLICT ("DateKey") DO UPDATE
                        SET "TenYearYield" = COALESCE(EXCLUDED."TenYearYield", "MacroIndicators"."TenYearYield"),
                            "TwoYearYield" = COALESCE(EXCLUDED."TwoYearYield", "MacroIndicators"."TwoYearYield"),
                            "CrudeOilPrice" = COALESCE(EXCLUDED."CrudeOilPrice", "MacroIndicators"."CrudeOilPrice"),
                            "BitcoinPrice" = COALESCE(EXCLUDED."BitcoinPrice", "MacroIndicators"."BitcoinPrice"),
                            "KospiIndex" = COALESCE(EXCLUDED."KospiIndex", "MacroIndicators"."KospiIndex"),
                            "SP500Index" = COALESCE(EXCLUDED."SP500Index", "MacroIndicators"."SP500Index"),
                            "NasdaqIndex" = COALESCE(EXCLUDED."NasdaqIndex", "MacroIndicators"."NasdaqIndex"),
                            "RSPPrice" = COALESCE(EXCLUDED."RSPPrice", "MacroIndicators"."RSPPrice"),
                            "TenYearYieldChangePct" = COALESCE(EXCLUDED."TenYearYieldChangePct", "MacroIndicators"."TenYearYieldChangePct"),
                            "TwoYearYieldChangePct" = COALESCE(EXCLUDED."TwoYearYieldChangePct", "MacroIndicators"."TwoYearYieldChangePct"),
                            "CrudeOilChangePct" = COALESCE(EXCLUDED."CrudeOilChangePct", "MacroIndicators"."CrudeOilChangePct"),
                            "BitcoinChangePct" = COALESCE(EXCLUDED."BitcoinChangePct", "MacroIndicators"."BitcoinChangePct"),
                            "KospiChangePct" = COALESCE(EXCLUDED."KospiChangePct", "MacroIndicators"."KospiChangePct"),
                            "SP500ChangePct" = COALESCE(EXCLUDED."SP500ChangePct", "MacroIndicators"."SP500ChangePct"),
                            "NasdaqChangePct" = COALESCE(EXCLUDED."NasdaqChangePct", "MacroIndicators"."NasdaqChangePct"),
                            "RSPChangePct" = COALESCE(EXCLUDED."RSPChangePct", "MacroIndicators"."RSPChangePct")
                    """
                ),
                {
                    "date_key": date_key,
                    "ten_year": float(ten_year.get(d)) if d in ten_year.index else None,
                    "two_year": float(two_year.get(d)) if d in two_year.index else None,
                    "oil": oil_by_date.get(d),
                    "bitcoin": bitcoin_by_date.get(d),
                    "kospi": kospi_by_date.get(d),
                    "sp500": sp500_by_date.get(d),
                    "nasdaq": nasdaq_by_date.get(d),
                    "rsp": rsp_by_date.get(d),
                    "ten_year_chg": ten_year_chg_by_date.get(d),
                    "two_year_chg": two_year_chg_by_date.get(d),
                    "oil_chg": oil_chg_by_date.get(d),
                    "bitcoin_chg": bitcoin_chg_by_date.get(d),
                    "kospi_chg": kospi_chg_by_date.get(d),
                    "sp500_chg": sp500_chg_by_date.get(d),
                    "nasdaq_chg": nasdaq_chg_by_date.get(d),
                    "rsp_chg": rsp_chg_by_date.get(d),
                },
            )
            n += 1
    print(f"Upserted {n} MacroIndicators rows ({all_dates[0].date()} to {all_dates[-1].date()})")


if __name__ == "__main__":
    main()
