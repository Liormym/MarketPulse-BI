"""Thin read-only data-access layer for the webapp.

Reuses the same SQLAlchemy engine/settings as the pipeline (src/marketpulse)
so connection config stays in one place (.env / config.py).
"""
import sys
from pathlib import Path

SRC_PATH = str(Path(__file__).resolve().parents[1] / "src")
if SRC_PATH not in sys.path:
    sys.path.insert(0, SRC_PATH)

from sqlalchemy import text  # noqa: E402

from marketpulse.load.db import get_engine  # noqa: E402
from marketpulse.sector_flow import SECTOR_SPDR_TICKERS, SECTOR_TO_SPDR  # noqa: E402


def get_asset_key(ticker: str) -> int | None:
    with get_engine().connect() as conn:
        row = conn.execute(
            text('SELECT "AssetKey" FROM "DimAsset" WHERE "Ticker" = :ticker'), {"ticker": ticker}
        ).first()
    return row[0] if row else None


def get_tickers() -> list[dict]:
    """All assets from DimAsset, for populating the ticker picker."""
    query = text(
        """
        SELECT "Ticker", "CompanyName", "Sector"
        FROM "DimAsset"
        ORDER BY "Ticker"
        """
    )
    with get_engine().connect() as conn:
        rows = conn.execute(query).mappings().all()
    return [
        {"ticker": r["Ticker"], "name": r["CompanyName"], "sector": r["Sector"]}
        for r in rows
    ]


def get_price_sentiment_history(ticker: str) -> dict | None:
    """Daily close price + aggregated sentiment for one ticker, joined on DimDate.

    Returns None if the ticker isn't in DimAsset at all; returns an entry with
    empty series if the asset exists but has no FactDailyPrice rows yet.
    """
    asset_query = text(
        """
        SELECT "AssetKey", "Ticker", "CompanyName", "Sector"
        FROM "DimAsset"
        WHERE "Ticker" = :ticker
        """
    )
    history_query = text(
        """
        SELECT
            d."Date"                AS date,
            p."Close"               AS close,
            p."Open"                AS open,
            p."Volume"              AS volume,
            s."AvgSentimentScore"   AS sentiment,
            t."SMA20"               AS sma20,
            t."SMA50"               AS sma50,
            t."SMA150"              AS sma150,
            t."SMA200"              AS sma200,
            t."GapPct"              AS gap_pct
        FROM "FactDailyPrice" p
        JOIN "DimDate" d ON d."DateKey" = p."DateKey"
        LEFT JOIN "FactSentiment" s
            ON s."AssetKey" = p."AssetKey" AND s."DateKey" = p."DateKey"
        LEFT JOIN "FactStockTechnicals" t
            ON t."AssetKey" = p."AssetKey" AND t."DateKey" = p."DateKey"
        WHERE p."AssetKey" = :asset_key
        ORDER BY d."Date" ASC
        """
    )
    latest_technicals_query = text(
        """
        SELECT "ATR14", "AvgVolume20D"
        FROM "FactStockTechnicals"
        WHERE "AssetKey" = :asset_key
        ORDER BY "DateKey" DESC
        LIMIT 1
        """
    )

    def _round(value, ndigits):
        return round(value, ndigits) if value is not None else None

    with get_engine().connect() as conn:
        asset = conn.execute(asset_query, {"ticker": ticker}).mappings().first()
        if asset is None:
            return None

        rows = conn.execute(history_query, {"asset_key": asset["AssetKey"]}).mappings().all()
        latest_technicals = conn.execute(
            latest_technicals_query, {"asset_key": asset["AssetKey"]}
        ).mappings().first()

    sector_spdr = SECTOR_TO_SPDR.get(asset["Sector"])

    return {
        "ticker": asset["Ticker"],
        "name": asset["CompanyName"],
        "sector": asset["Sector"],
        "sector_spdr": sector_spdr,
        "dates": [r["date"].isoformat() for r in rows],
        "prices": [_round(r["close"], 2) for r in rows],
        "opens": [_round(r["open"], 2) for r in rows],
        "volumes": [r["volume"] for r in rows],
        "sentiment": [_round(r["sentiment"], 3) for r in rows],
        "sma20": [_round(r["sma20"], 2) for r in rows],
        "sma50": [_round(r["sma50"], 2) for r in rows],
        "sma150": [_round(r["sma150"], 2) for r in rows],
        "sma200": [_round(r["sma200"], 2) for r in rows],
        "gap_pct": [_round(r["gap_pct"], 2) for r in rows],
        "atr14": _round(latest_technicals["ATR14"], 2) if latest_technicals else None,
        "avg_volume_20d": _round(latest_technicals["AvgVolume20D"], 0) if latest_technicals else None,
    }


def get_macro_snapshot() -> dict | None:
    """Latest known value for each macro indicator, independently - yields
    (FRED) and oil (yfinance) don't always publish for the same trading day,
    so picking a single "latest row" can null out a field that actually has
    a more recent value a day or two back."""
    query = text(
        """
        SELECT
            (SELECT MAX(d."Date") FROM "MacroIndicators" m JOIN "DimDate" d ON d."DateKey" = m."DateKey"
                WHERE m."TenYearYield" IS NOT NULL) AS ten_year_as_of,
            (SELECT m."TenYearYield" FROM "MacroIndicators" m JOIN "DimDate" d ON d."DateKey" = m."DateKey"
                WHERE m."TenYearYield" IS NOT NULL ORDER BY d."Date" DESC LIMIT 1) AS ten_year_yield,
            (SELECT m."TwoYearYield" FROM "MacroIndicators" m JOIN "DimDate" d ON d."DateKey" = m."DateKey"
                WHERE m."TwoYearYield" IS NOT NULL ORDER BY d."Date" DESC LIMIT 1) AS two_year_yield,
            (SELECT MAX(d."Date") FROM "MacroIndicators" m JOIN "DimDate" d ON d."DateKey" = m."DateKey"
                WHERE m."CrudeOilPrice" IS NOT NULL) AS oil_as_of,
            (SELECT m."CrudeOilPrice" FROM "MacroIndicators" m JOIN "DimDate" d ON d."DateKey" = m."DateKey"
                WHERE m."CrudeOilPrice" IS NOT NULL ORDER BY d."Date" DESC LIMIT 1) AS crude_oil_price,
            (SELECT m."MarketBreadth" FROM "MacroIndicators" m JOIN "DimDate" d ON d."DateKey" = m."DateKey"
                WHERE m."MarketBreadth" IS NOT NULL ORDER BY d."Date" DESC LIMIT 1) AS market_breadth,
            (SELECT m."AAIISentiment" FROM "MacroIndicators" m JOIN "DimDate" d ON d."DateKey" = m."DateKey"
                WHERE m."AAIISentiment" IS NOT NULL ORDER BY d."Date" DESC LIMIT 1) AS aaii_sentiment
        """
    )
    with get_engine().connect() as conn:
        row = conn.execute(query).mappings().first()
    if row is None or (row["ten_year_as_of"] is None and row["oil_as_of"] is None):
        return None
    as_of = max(d for d in (row["ten_year_as_of"], row["oil_as_of"]) if d is not None)
    return {
        "as_of": as_of.isoformat(),
        "ten_year_yield": row["ten_year_yield"],
        "two_year_yield": row["two_year_yield"],
        "crude_oil_price": row["crude_oil_price"],
        "market_breadth": row["market_breadth"],
        "aaii_sentiment": row["aaii_sentiment"],
    }


def get_sector_flows() -> list[dict]:
    """Latest Accumulation/Distribution/Neutral status for all 11 sector SPDRs."""
    query = text(
        """
        SELECT DISTINCT ON (a."Ticker")
            a."Ticker", a."CompanyName", d."Date",
            fsv."Volume", fsv."AvgVolume20D", fsv."VolumeRatio", fsv."FlowStatus"
        FROM "FactSectorVolume" fsv
        JOIN "DimAsset" a ON a."AssetKey" = fsv."AssetKey"
        JOIN "DimDate" d ON d."DateKey" = fsv."DateKey"
        WHERE a."Ticker" = ANY(:tickers)
        ORDER BY a."Ticker", d."Date" DESC
        """
    )
    with get_engine().connect() as conn:
        rows = conn.execute(query, {"tickers": SECTOR_SPDR_TICKERS}).mappings().all()
    return [
        {
            "ticker": r["Ticker"],
            "name": r["CompanyName"],
            "as_of": r["Date"].isoformat(),
            "volume_ratio": round(r["VolumeRatio"], 2) if r["VolumeRatio"] is not None else None,
            "flow_status": r["FlowStatus"],
        }
        for r in rows
    ]


def get_top_movers(limit: int = 25) -> list[dict]:
    """Largest daily % movers (by absolute value) across the whole watchlist,
    using each ticker's latest two trading days."""
    query = text(
        """
        WITH ranked AS (
            SELECT
                p."AssetKey", d."Date", p."Close",
                LAG(p."Close") OVER (PARTITION BY p."AssetKey" ORDER BY d."Date") AS prev_close,
                ROW_NUMBER() OVER (PARTITION BY p."AssetKey" ORDER BY d."Date" DESC) AS rn
            FROM "FactDailyPrice" p
            JOIN "DimDate" d ON d."DateKey" = p."DateKey"
        )
        SELECT a."Ticker", a."CompanyName", r."Close", r."prev_close"
        FROM ranked r
        JOIN "DimAsset" a ON a."AssetKey" = r."AssetKey"
        WHERE r.rn = 1 AND r."prev_close" IS NOT NULL AND r."prev_close" != 0
        """
    )
    with get_engine().connect() as conn:
        rows = conn.execute(query).mappings().all()

    movers = []
    for r in rows:
        change_pct = (r["Close"] - r["prev_close"]) / r["prev_close"] * 100
        movers.append(
            {
                "ticker": r["Ticker"],
                "name": r["CompanyName"],
                "price": round(r["Close"], 2),
                "change_pct": round(change_pct, 2),
            }
        )
    movers.sort(key=lambda m: abs(m["change_pct"]), reverse=True)
    return movers[:limit]


def enrich_stock(ticker: str) -> dict | None:
    """Short interest + insider transactions, fetched on-demand and cached
    (see enrichment.py - these are point-in-time snapshots, not daily
    history, so they're not part of the batch technicals computation)."""
    from enrichment import get_or_fetch_enrichment

    asset_key = get_asset_key(ticker)
    if asset_key is None:
        return None
    with get_engine().begin() as conn:
        return get_or_fetch_enrichment(conn, asset_key, ticker)
