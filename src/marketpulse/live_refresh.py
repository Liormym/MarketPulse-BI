"""Live, single-ticker refresh: price/volume, news/sentiment, and technicals -
triggered by the webapp's "Refresh Data" button (rate-limited there, see
webapp/rate_limit.py).

Deliberately lighter-weight than the full pipeline (src/marketpulse/pipeline.py):
no DQ checks, no PipelineExecutionLog row. This is a small, targeted,
user-triggered top-up for one ticker, not a pipeline run.

Scope note: this refreshes the ticker's OWN price/sentiment/technicals only.
It does not recompute sector-wide FactSectorVolume or the shared
MacroIndicators - those aren't specific to one stock, and refreshing them on
every per-stock click would be wasteful and inconsistent across users.
"""
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.engine import Engine

from .aggregate import ScoredArticle, aggregate_daily_sentiment
from .extract.news import fetch_news_for_ticker
from .extract.prices import fetch_prices
from .load.upsert import upsert_daily_prices, upsert_daily_sentiment, upsert_news_articles
from .sentiment.finbert import classify
from .technicals import compute_and_upsert_technicals_for_asset

REFRESH_PRICE_PERIOD = "5d"  # enough to catch the latest trading day even across a weekend/holiday
REFRESH_NEWS_LIMIT = 15


@dataclass
class RefreshResult:
    ticker: str
    failed: bool
    price_rows: int = 0
    articles_fetched: int = 0
    technicals_rows: int = 0
    error: str | None = None


def _ensure_dim_dates(conn, trade_dates) -> None:
    for d in sorted(set(trade_dates)):
        conn.execute(
            text(
                """
                INSERT INTO "DimDate" ("DateKey", "Date", "IsTradingDay")
                VALUES (:date_key, :date, TRUE)
                ON CONFLICT ("DateKey") DO NOTHING
                """
            ),
            {"date_key": int(d.strftime("%Y%m%d")), "date": d},
        )


def refresh_ticker(engine: Engine, ticker: str, company_name: str) -> RefreshResult:
    with engine.connect() as conn:
        asset = conn.execute(
            text('SELECT "AssetKey" FROM "DimAsset" WHERE "Ticker" = :t'), {"t": ticker}
        ).first()
    if asset is None:
        return RefreshResult(ticker, failed=True, error=f"Unknown ticker '{ticker}'")
    asset_key = asset[0]
    asset_keys = {ticker: asset_key}

    # --- Price / Volume ---
    price_records, failed_tickers = fetch_prices([ticker], period=REFRESH_PRICE_PERIOD)
    if failed_tickers:
        return RefreshResult(ticker, failed=True, error="yfinance price fetch failed")

    with engine.begin() as conn:
        _ensure_dim_dates(conn, (r.trade_date for r in price_records))
        price_rows = upsert_daily_prices(conn, price_records, asset_keys)

    # --- News / Sentiment ---
    news_records = [r for r in fetch_news_for_ticker(ticker, company_name, REFRESH_NEWS_LIMIT) if r.title.strip()]
    scored_articles = []
    for r in news_records:
        result = classify(r.title)
        r.category = result.category if result else None
        r.score = result.score if result else None
        scored_articles.append(
            ScoredArticle(ticker=ticker, trade_date=r.published_at.date(), category=r.category, score=r.score)
        )

    with engine.begin() as conn:
        if news_records:
            upsert_news_articles(conn, news_records, asset_keys)
        daily_sentiment = aggregate_daily_sentiment(scored_articles)
        if daily_sentiment:
            upsert_daily_sentiment(conn, daily_sentiment, asset_keys)

    # --- Technicals (this ticker only - see module docstring for scope) ---
    with engine.begin() as conn:
        technicals_rows = compute_and_upsert_technicals_for_asset(conn, asset_key)

    return RefreshResult(
        ticker,
        failed=False,
        price_rows=price_rows,
        articles_fetched=len(news_records),
        technicals_rows=technicals_rows,
    )
