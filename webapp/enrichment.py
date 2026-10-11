"""On-demand stock enrichment: short interest, fundamentals (trailing P/E,
market cap, beta) and insider transactions.

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
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import yfinance as yf
from sqlalchemy import text

SRC_PATH = str(Path(__file__).resolve().parents[1] / "src")
if SRC_PATH not in sys.path:
    sys.path.insert(0, SRC_PATH)

from marketpulse.insider import (  # noqa: E402
    INSIDER_LOOKBACK_DAYS,
    classify_transaction,
    is_ceo_cfo,
    net_insider_activity,
)

CACHE_TTL = timedelta(hours=24)
DISPLAY_TRANSACTION_LIMIT = 10


def _clean_float(value):
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    return float(value)


def _clean_int(value):
    cleaned = _clean_float(value)
    return int(cleaned) if cleaned is not None else None


def _finite_number(value):
    """A real, finite number or None. yfinance sometimes hands back strings
    like "Infinity" (e.g. a P/E on near-zero earnings) that float() accepts."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _fetch_info_snapshot(ticker: str) -> dict | None:
    """Short interest + fundamentals from ONE Ticker.info call. Returns None if
    the call itself failed (so the caller keeps whatever it already cached
    rather than overwriting good values with blanks); a successful call for
    something with no P/E (loss-making company, ETF, crypto) returns a dict
    with None for that field."""
    try:
        info = yf.Ticker(ticker).info
    except Exception:
        return None

    pe = _finite_number(info.get("trailingPE"))
    market_cap = _finite_number(info.get("marketCap"))
    return {
        "short_percent_of_float": _finite_number(info.get("shortPercentOfFloat")),
        "trailing_pe": pe if pe is not None and pe > 0 else None,
        "market_cap": int(market_cap) if market_cap is not None and market_cap > 0 else None,
        "beta": _finite_number(info.get("beta")),
    }


def _fetch_insider_transactions(ticker: str) -> list[dict] | None:
    """None means the fetch FAILED (the caller must not advance the freshness
    timestamp); an empty list means the call worked and there are no filings."""
    try:
        df = yf.Ticker(ticker).insider_transactions
    except Exception:
        return None
    if df is None or df.empty:
        return []

    records = []
    for _, row in df.iterrows():
        txn_type = classify_transaction(row.get("Text"))
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
                "is_executive_sale": txn_type == "Sale" and is_ceo_cfo(row.get("Position")),
                "is_executive_buy": txn_type == "Buy" and is_ceo_cfo(row.get("Position")),
            }
        )
    return records


def get_or_fetch_enrichment(conn, asset_key: int, ticker: str) -> dict:
    """Returns {"short_percent_of_float", "fundamentals", "insider_net",
    "insider_activity", "insider_transactions"}, refreshing short interest,
    fundamentals and insider filings from yfinance when their cached copy is
    missing or older than CACHE_TTL. `insider_net` is "selling", "buying" or None:
    the CEO/CFO net direction over the last INSIDER_LOOKBACK_DAYS days.
    """
    cached = conn.execute(
        text(
            '''SELECT "ShortPercentOfFloat", "FetchedAt", "TrailingPE", "MarketCap", "Beta", "FundamentalsFetchedAt",
                      "InsiderFetchedAt"
               FROM "StockEnrichmentCache" WHERE "AssetKey" = :asset_key'''
        ),
        {"asset_key": asset_key},
    ).first()

    is_stale = (
        cached is None
        or cached[5] is None  # row written before the fundamentals columns existed
        or (datetime.now(timezone.utc) - cached[1]) > CACHE_TTL
    )
    snapshot = _fetch_info_snapshot(ticker) if is_stale else None

    if snapshot is not None:
        conn.execute(
            text(
                """
                INSERT INTO "StockEnrichmentCache"
                    ("AssetKey", "ShortPercentOfFloat", "TrailingPE", "MarketCap", "Beta", "FetchedAt", "FundamentalsFetchedAt")
                VALUES (:asset_key, :short_pct, :pe, :market_cap, :beta, now(), now())
                ON CONFLICT ("AssetKey") DO UPDATE
                    SET "ShortPercentOfFloat" = EXCLUDED."ShortPercentOfFloat",
                        "TrailingPE" = EXCLUDED."TrailingPE",
                        "MarketCap" = EXCLUDED."MarketCap",
                        "Beta" = EXCLUDED."Beta",
                        "FetchedAt" = EXCLUDED."FetchedAt",
                        "FundamentalsFetchedAt" = EXCLUDED."FundamentalsFetchedAt"
                """
            ),
            {
                "asset_key": asset_key,
                "short_pct": snapshot["short_percent_of_float"],
                "pe": snapshot["trailing_pe"],
                "market_cap": snapshot["market_cap"],
                "beta": snapshot["beta"],
            },
        )
        short_pct = snapshot["short_percent_of_float"]
        fundamentals = {
            "trailing_pe": snapshot["trailing_pe"],
            "market_cap": snapshot["market_cap"],
            "beta": snapshot["beta"],
            "as_of": datetime.now(timezone.utc).isoformat(),
        }
    elif cached is not None:
        # Fresh cache, or a refresh that failed: serve what we have.
        short_pct = cached[0]
        fundamentals = {
            "trailing_pe": cached[2],
            "market_cap": cached[3],
            "beta": cached[4],
            "as_of": cached[5].isoformat() if cached[5] else None,
        }
    else:
        short_pct = None
        fundamentals = {"trailing_pe": None, "market_cap": None, "beta": None, "as_of": None}

    insider_stale = (
        cached is None
        or cached[6] is None  # never fetched, or flagged for re-classification by a migration
        or (datetime.now(timezone.utc) - cached[6]) > CACHE_TTL
    )
    fetched_insiders = _fetch_insider_transactions(ticker) if insider_stale else None
    if fetched_insiders is not None:
        for txn in fetched_insiders:
            conn.execute(
                text(
                    """
                    INSERT INTO "InsiderTransactions"
                        ("TransactionID", "AssetKey", "InsiderName", "Position", "TransactionType",
                         "Shares", "Value", "TransactionDate", "IsExecutiveSale", "IsExecutiveBuy")
                    VALUES (:transaction_id, :asset_key, :insider_name, :position, :transaction_type,
                            :shares, :value, :transaction_date, :is_executive_sale, :is_executive_buy)
                    ON CONFLICT ("TransactionID") DO UPDATE
                        SET "Position" = EXCLUDED."Position",
                            "TransactionType" = EXCLUDED."TransactionType",
                            "IsExecutiveSale" = EXCLUDED."IsExecutiveSale",
                            "IsExecutiveBuy" = EXCLUDED."IsExecutiveBuy"
                    """
                ),
                {**txn, "asset_key": asset_key},
            )
        # Only after a successful fetch. A brand-new cache row is created already
        # stale for the short-interest snapshot (epoch), so it is fetched next time.
        conn.execute(
            text(
                """
                INSERT INTO "StockEnrichmentCache" ("AssetKey", "FetchedAt", "InsiderFetchedAt")
                VALUES (:asset_key, to_timestamp(0), now())
                ON CONFLICT ("AssetKey") DO UPDATE SET "InsiderFetchedAt" = now()
                """
            ),
            {"asset_key": asset_key},
        )

    transactions = conn.execute(
        text(
            """
            SELECT "InsiderName", "Position", "TransactionType", "Shares", "Value",
                   "TransactionDate", "IsExecutiveSale", "IsExecutiveBuy"
            FROM "InsiderTransactions"
            WHERE "AssetKey" = :asset_key
            ORDER BY "TransactionDate" DESC
            LIMIT :limit
            """
        ),
        {"asset_key": asset_key, "limit": DISPLAY_TRANSACTION_LIMIT},
    ).mappings().all()

    # Deliberately NOT derived from the (LIMIT'd) `transactions` list above: a
    # display list capped at DISPLAY_TRANSACTION_LIMIT can easily exclude a CEO/CFO
    # trade that is still inside the scoring window.
    cutoff = date.today() - timedelta(days=INSIDER_LOOKBACK_DAYS)
    window_rows = conn.execute(
        text(
            """
            SELECT "TransactionDate", "Value", "Shares", "IsExecutiveSale", "IsExecutiveBuy"
            FROM "InsiderTransactions"
            WHERE "AssetKey" = :asset_key AND "TransactionDate" >= :cutoff
              AND ("IsExecutiveSale" OR "IsExecutiveBuy")
            """
        ),
        {"asset_key": asset_key, "cutoff": cutoff},
    ).mappings().all()
    insider_activity = net_insider_activity(
        [
            {
                "transaction_date": r["TransactionDate"],
                "value": r["Value"],
                "shares": r["Shares"],
                "is_executive_sale": r["IsExecutiveSale"],
                "is_executive_buy": r["IsExecutiveBuy"],
            }
            for r in window_rows
        ]
    )

    return {
        "short_percent_of_float": short_pct,
        "fundamentals": fundamentals,
        "insider_net": insider_activity["net"],
        "insider_activity": insider_activity,
        "insider_transactions": [
            {
                "insider_name": t["InsiderName"],
                "position": t["Position"],
                "transaction_type": t["TransactionType"],
                "shares": t["Shares"],
                "value": t["Value"],
                "transaction_date": t["TransactionDate"].isoformat() if t["TransactionDate"] else None,
                "is_executive_sale": t["IsExecutiveSale"],
                "is_executive_buy": t["IsExecutiveBuy"],
            }
            for t in transactions
        ],
    }
