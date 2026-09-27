"""Batch-computes the Investment Score for every watchlist ticker with
enough price history and caches it in FactInvestmentScore, so the
dashboard's "Top 5 Strong Buys" widget is a fast indexed SELECT instead of
recomputing ~500+ per-ticker scores (each needing price/technicals/
sentiment/sector-flow joins) on every page load.

Short interest / insider-sale enrichment uses whatever is already cached in
StockEnrichmentCache/InsiderTransactions - this script does NOT trigger new
yfinance fetches per ticker. That fetch-on-view + cache is enrichment.py's
job (see webapp/db.py's enrich_stock, called only for the single ticker a
user is actively viewing); doing it here for the whole watchlist would mean
500+ live yfinance calls on every run.

Skips non-equity assets (Sector "Index"/"ETF") - the watchlist also tracks
macro reference instruments (e.g. ^KS11/KOSPI, the sector SPDR ETFs used by
sector_flow.py) alongside individual stocks. An index literally can't be
bought, and an ETF is already surfaced elsewhere as the sector-flow
reference, not a stock pick - scoring and recommending either as a "Strong
Buy" would be actionable nonsense, not just noise.

Usage: PYTHONPATH=src:webapp .venv/bin/python scripts/compute_investment_scores.py
"""
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "webapp"))

from sqlalchemy import text  # noqa: E402

import db as webapp_db  # noqa: E402
from marketpulse.load.db import get_engine  # noqa: E402
from marketpulse.scoring import SENTIMENT_LOOKBACK_DAYS, compute_investment_score  # noqa: E402

NON_EQUITY_SECTORS = {"Index", "ETF"}


def _cached_enrichment(conn, asset_key: int) -> tuple[float | None, bool]:
    """Reads whatever short-interest/insider-sale data is already cached for
    this asset, without triggering a fresh yfinance fetch (see module
    docstring)."""
    row = conn.execute(
        text('SELECT "ShortPercentOfFloat" FROM "StockEnrichmentCache" WHERE "AssetKey" = :asset_key'),
        {"asset_key": asset_key},
    ).first()
    short_percent_of_float = row[0] if row else None

    exec_sale = conn.execute(
        text(
            """
            SELECT 1 FROM "InsiderTransactions"
            WHERE "AssetKey" = :asset_key AND "IsExecutiveSale" = TRUE
              AND "TransactionDate" >= CURRENT_DATE - INTERVAL '90 days'
            LIMIT 1
            """
        ),
        {"asset_key": asset_key},
    ).first()
    return short_percent_of_float, exec_sale is not None


def main() -> None:
    engine = get_engine()
    # Sector flow is keyed by the sector's SPDR ETF ticker (e.g. "XLK"), the
    # same lookup db.get_price_sentiment_history() resolves per stock as
    # data["sector_spdr"].
    sector_flows = {s["ticker"]: s for s in webapp_db.get_sector_flows()}

    tickers = webapp_db.get_tickers()
    n_scored = 0
    n_skipped = 0

    with engine.begin() as conn:
        for t in tickers:
            ticker = t["ticker"]
            if t["sector"] in NON_EQUITY_SECTORS:
                n_skipped += 1
                continue

            asset_key = webapp_db.get_asset_key(ticker)
            if asset_key is None:
                n_skipped += 1
                continue

            data = webapp_db.get_price_sentiment_history(ticker)
            if data is None:
                n_skipped += 1
                continue

            sector_flow = sector_flows.get(data["sector_spdr"])
            short_percent_of_float, has_recent_executive_sale = _cached_enrichment(conn, asset_key)

            breakdown = compute_investment_score(
                closes=data["prices"],
                volumes=data["volumes"],
                sma20_series=data["sma20"],
                sma50_series=data["sma50"],
                sma200_series=data["sma200"],
                recent_sentiment=data["sentiment"][-SENTIMENT_LOOKBACK_DAYS:],
                atr14=data["atr14"],
                atr_90d_avg=data["atr_90d_avg"],
                atr_90d_std=data["atr_90d_std"],
                avg_volume_20d=data["avg_volume_20d"],
                sector_flow_status=sector_flow["flow_status"] if sector_flow else None,
                short_percent_of_float=short_percent_of_float,
                has_recent_executive_sale=has_recent_executive_sale,
            )
            if breakdown.score is None:
                n_skipped += 1
                continue

            conn.execute(
                text(
                    """
                    INSERT INTO "FactInvestmentScore" ("AssetKey", "Score", "ComputedAt")
                    VALUES (:asset_key, :score, :computed_at)
                    ON CONFLICT ("AssetKey") DO UPDATE
                        SET "Score" = EXCLUDED."Score", "ComputedAt" = EXCLUDED."ComputedAt"
                    """
                ),
                {"asset_key": asset_key, "score": breakdown.score, "computed_at": datetime.now(timezone.utc)},
            )
            n_scored += 1

    print(f"Scored {n_scored} tickers, skipped {n_skipped} (insufficient price history)")


if __name__ == "__main__":
    main()
