"""On-demand stock enrichment: short interest and insider transactions.

Unlike price/technicals (precomputed in batch for all 560 tickers, since
they're pure computation over already-backfilled data), these come straight
from yfinance per-ticker API calls and are point-in-time snapshots, not
daily time series - short interest updates ~biweekly at the source, and
insider transactions are individual SEC Form 4 filings. Fetching all 560
tickers upfront would be slow and mostly wasted, since the deep-dive screen
only ever looks at one ticker at a time. So these are fetched lazily, right
when /stock/<ticker> is requested, and cached in the DB.
"""
import hashlib
import math
from datetime import datetime, timedelta, timezone

import yfinance as yf
from sqlalchemy import text

CACHE_TTL = timedelta(hours=24)


def _clean_float(value):
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    return float(value)


def _clean_int(value):
    cleaned = _clean_float(value)
    return int(cleaned) if cleaned is not None else None


def _classify_transaction_text(raw_text: str | None) -> str | None:
    t = (raw_text or "").strip().lower()
    if t.startswith("sale"):
        return "Sale"
    if t.startswith("stock gift"):
        return "Gift"
    if t:
        return "Other"
    return None  # no discernible transaction type (often an unpriced option exercise/conversion)


def _is_key_officer(position: str | None) -> bool:
    p = (position or "").lower()
    return "chief" in p or "ceo" in p


def _fetch_short_percent_of_float(ticker: str) -> float | None:
    try:
        info = yf.Ticker(ticker).info
    except Exception:
        return None
    return _clean_float(info.get("shortPercentOfFloat"))


def _fetch_insider_transactions(ticker: str) -> list[dict]:
    try:
        df = yf.Ticker(ticker).insider_transactions
    except Exception:
        return []
    if df is None or df.empty:
        return []

    records = []
    for _, row in df.iterrows():
        txn_type = _classify_transaction_text(row.get("Text"))
        if txn_type is None:
            continue

        insider = str(row.get("Insider") or "").strip()
        start_date = row.get("Start Date")
        shares = _clean_int(row.get("Shares"))
        value = _clean_float(row.get("Value"))

        key_material = f"{ticker}|{insider}|{start_date}|{shares}|{value}"
        transaction_id = hashlib.sha256(key_material.encode()).hexdigest()

        records.append(
            {
                "transaction_id": transaction_id,
                "insider_name": insider,
                "position": row.get("Position"),
                "transaction_type": txn_type,
                "shares": shares,
                "value": value,
                "transaction_date": start_date.date() if hasattr(start_date, "date") else start_date,
                "is_executive_sale": txn_type == "Sale" and _is_key_officer(row.get("Position")),
            }
        )
    return records


def get_or_fetch_enrichment(conn, asset_key: int, ticker: str) -> dict:
    """Returns {"short_percent_of_float": float|None, "insider_transactions": [...]},
    refreshing from yfinance if the cached snapshot is missing or stale.
    """
    cached = conn.execute(
        text('SELECT "ShortPercentOfFloat", "FetchedAt" FROM "StockEnrichmentCache" WHERE "AssetKey" = :asset_key'),
        {"asset_key": asset_key},
    ).first()

    is_stale = cached is None or (datetime.now(timezone.utc) - cached[1]) > CACHE_TTL
    if is_stale:
        short_pct = _fetch_short_percent_of_float(ticker)
        conn.execute(
            text(
                """
                INSERT INTO "StockEnrichmentCache" ("AssetKey", "ShortPercentOfFloat", "FetchedAt")
                VALUES (:asset_key, :short_pct, now())
                ON CONFLICT ("AssetKey") DO UPDATE
                    SET "ShortPercentOfFloat" = EXCLUDED."ShortPercentOfFloat", "FetchedAt" = EXCLUDED."FetchedAt"
                """
            ),
            {"asset_key": asset_key, "short_pct": short_pct},
        )
    else:
        short_pct = cached[0]

    has_transactions = conn.execute(
        text('SELECT 1 FROM "InsiderTransactions" WHERE "AssetKey" = :asset_key LIMIT 1'),
        {"asset_key": asset_key},
    ).first()
    if has_transactions is None:
        for txn in _fetch_insider_transactions(ticker):
            conn.execute(
                text(
                    """
                    INSERT INTO "InsiderTransactions"
                        ("TransactionID", "AssetKey", "InsiderName", "Position", "TransactionType",
                         "Shares", "Value", "TransactionDate", "IsExecutiveSale")
                    VALUES (:transaction_id, :asset_key, :insider_name, :position, :transaction_type,
                            :shares, :value, :transaction_date, :is_executive_sale)
                    ON CONFLICT ("TransactionID") DO NOTHING
                    """
                ),
                {**txn, "asset_key": asset_key},
            )

    transactions = conn.execute(
        text(
            """
            SELECT "InsiderName", "Position", "TransactionType", "Shares", "Value",
                   "TransactionDate", "IsExecutiveSale"
            FROM "InsiderTransactions"
            WHERE "AssetKey" = :asset_key
            ORDER BY "TransactionDate" DESC
            LIMIT 10
            """
        ),
        {"asset_key": asset_key},
    ).mappings().all()

    return {
        "short_percent_of_float": short_pct,
        "insider_transactions": [
            {
                "insider_name": t["InsiderName"],
                "position": t["Position"],
                "transaction_type": t["TransactionType"],
                "shares": t["Shares"],
                "value": t["Value"],
                "transaction_date": t["TransactionDate"].isoformat() if t["TransactionDate"] else None,
                "is_executive_sale": t["IsExecutiveSale"],
            }
            for t in transactions
        ],
    }
