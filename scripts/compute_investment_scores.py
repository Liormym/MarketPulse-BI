"""Batch-computes the Investment Score for every watchlist ticker with
enough price history and caches it in FactInvestmentScore, so the
dashboard's "Top 5 Strong Buys" widget is a fast indexed SELECT instead of
recomputing ~500+ per-ticker scores (each needing price/technicals/
sentiment/sector-flow joins) on every page load.

compute_score_for_ticker() below runs the EXACT SAME pipeline as the live
/api/stock/<ticker> route (webapp/app.py's api_stock): get_price_sentiment_
history + get_sector_flows + enrich_stock, assembled into
compute_investment_score()'s kwargs by the shared db.build_score_kwargs().
This used to be two independently hand-written pipelines, and they
silently diverged - this script's own enrichment lookup read only whatever
was already cached (no yfinance fetch), while the live route fetches fresh
on a cache miss via enrich_stock(). A ticker nobody had ever viewed yet
would score with no insider-selling penalty here but the correct penalty on
its own /stock/<ticker> page: the same ticker, two different numbers,
depending only on which screen you looked at. Routing both entry points
through one shared pipeline makes that class of bug structurally impossible
- see tests/test_score_consistency.py, which asserts the two entry points
agree.

One consequence of sharing enrich_stock(): this script now WILL trigger a
yfinance call for any ticker whose enrichment cache is empty or older than
enrichment.CACHE_TTL (24h), same as visiting that ticker's page would. Run
it on a schedule at that same ~24h cadence (like the other CronJobs) and
most tickers hit a warm cache; a fully cold run does mean ~550 yfinance
calls once.

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
from marketpulse.scoring import ScoreBreakdown, compute_investment_score  # noqa: E402

NON_EQUITY_SECTORS = {"Index", "ETF"}


def compute_score_for_ticker(ticker: str, sector_flows: dict | None = None) -> ScoreBreakdown | None:
    """Computes the Investment Score for one ticker via the exact pipeline
    the live route uses. `sector_flows` lets a batch run reuse one
    get_sector_flows() query across every ticker instead of re-querying it
    per ticker; omit it (e.g. from a single-ticker call) and it's fetched
    fresh. Returns None if the ticker has no price history at all."""
    data = webapp_db.get_price_sentiment_history(ticker)
    if data is None:
        return None

    if sector_flows is None:
        sector_flows = {s["ticker"]: s for s in webapp_db.get_sector_flows()}
    sector_flow = sector_flows.get(data["sector_spdr"])

    enrichment = webapp_db.enrich_stock(ticker)

    return compute_investment_score(**webapp_db.build_score_kwargs(data, sector_flow, enrichment))


def main() -> None:
    engine = get_engine()
    # Sector flow is keyed by the sector's SPDR ETF ticker (e.g. "XLK"), the
    # same lookup db.get_price_sentiment_history() resolves per stock as
    # data["sector_spdr"]. Fetched once and reused across every ticker below.
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

            breakdown = compute_score_for_ticker(ticker, sector_flows=sector_flows)
            if breakdown is None or breakdown.score is None:
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
