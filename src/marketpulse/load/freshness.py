"""Records WHEN data was refreshed (tables from migration 009), so every screen
can show a real timestamp instead of just a trading date.

All timestamps are written by the database clock (now()), in UTC. Callers own
the transaction. A timestamp is written only after the work it describes
succeeded, never before, so "updated at" can never be ahead of the data.
"""
from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import text
from sqlalchemy.engine import Connection

SOURCES = ("macro", "fx", "prices", "news", "sector_flows", "scores", "brief", "eod_update")
_STOCK_COLUMNS = {
    "prices": "PricesFetchedAt",
    "news": "NewsFetchedAt",
    "technicals": "TechnicalsComputedAt",
}


def mark_source(conn: Connection, source: str) -> None:
    if source not in SOURCES:
        raise ValueError(f"unknown freshness source {source!r}")
    conn.execute(
        text(
            """
            INSERT INTO "DataFreshness" ("Source", "RefreshedAt") VALUES (:s, now())
            ON CONFLICT ("Source") DO UPDATE SET "RefreshedAt" = now()
            """
        ),
        {"s": source},
    )


def mark_stocks(conn: Connection, asset_keys: Iterable[int], what: str, kind: str | None = None) -> None:
    """what: 'prices' | 'news' | 'technicals'. kind: 'eod' | 'manual' (leave None
    to keep the existing value, e.g. for technicals which follow either)."""
    column = _STOCK_COLUMNS[what]
    for asset_key in asset_keys:
        conn.execute(
            text(
                f"""
                INSERT INTO "StockDataFreshness" ("AssetKey", "{column}", "LastRefreshKind")
                VALUES (:a, now(), :kind)
                ON CONFLICT ("AssetKey") DO UPDATE
                    SET "{column}" = now(),
                        "LastRefreshKind" = COALESCE(:kind, "StockDataFreshness"."LastRefreshKind")
                """
            ),
            {"a": asset_key, "kind": kind},
        )
