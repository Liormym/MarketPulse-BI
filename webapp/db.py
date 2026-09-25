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
            s."AvgSentimentScore"   AS sentiment
        FROM "FactDailyPrice" p
        JOIN "DimDate" d ON d."DateKey" = p."DateKey"
        LEFT JOIN "FactSentiment" s
            ON s."AssetKey" = p."AssetKey" AND s."DateKey" = p."DateKey"
        WHERE p."AssetKey" = :asset_key
        ORDER BY d."Date" ASC
        """
    )

    with get_engine().connect() as conn:
        asset = conn.execute(asset_query, {"ticker": ticker}).mappings().first()
        if asset is None:
            return None

        rows = conn.execute(history_query, {"asset_key": asset["AssetKey"]}).mappings().all()

    return {
        "ticker": asset["Ticker"],
        "name": asset["CompanyName"],
        "sector": asset["Sector"],
        "dates": [r["date"].isoformat() for r in rows],
        "prices": [round(r["close"], 2) for r in rows],
        "sentiment": [
            round(r["sentiment"], 3) if r["sentiment"] is not None else None
            for r in rows
        ],
    }
