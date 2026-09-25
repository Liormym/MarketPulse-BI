"""One-time DB fix: recomputes FactSentiment.AvgSentimentScore for every
(AssetKey, DateKey) using the now confidence-weighted aggregate_daily_sentiment()
(see src/marketpulse/aggregate.py) instead of the plain mean it used before.
Existing rows were computed with the old plain-mean formula; this brings
them in line without needing to re-run the whole pipeline.

Safe to re-run: it's a pure recompute from NewsArticles, then an idempotent
upsert.

Usage: PYTHONPATH=src .venv/bin/python scripts/reweight_sentiment_aggregates.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sqlalchemy import text  # noqa: E402

from marketpulse.aggregate import ScoredArticle, aggregate_daily_sentiment  # noqa: E402
from marketpulse.load.db import get_engine  # noqa: E402
from marketpulse.load.upsert import get_asset_key_map, upsert_daily_sentiment  # noqa: E402


def main() -> None:
    engine = get_engine()

    with engine.connect() as conn:
        asset_keys = get_asset_key_map(conn)
        ticker_by_key = {v: k for k, v in asset_keys.items()}
        rows = conn.execute(
            text(
                """
                SELECT "AssetKey", "PublishedAt", "SentimentCategory", "SentimentScore"
                FROM "NewsArticles"
                """
            )
        ).all()

    articles = []
    for asset_key, published_at, category, score in rows:
        ticker = ticker_by_key.get(asset_key)
        if ticker is None:
            continue
        articles.append(
            ScoredArticle(ticker=ticker, trade_date=published_at.date(), category=category, score=score)
        )

    daily = aggregate_daily_sentiment(articles)
    print(f"Recomputed {len(daily)} FactSentiment rows from {len(articles)} articles")

    with engine.begin() as conn:
        n = upsert_daily_sentiment(conn, daily, asset_keys)
    print(f"Upserted {n} FactSentiment rows")


if __name__ == "__main__":
    main()
