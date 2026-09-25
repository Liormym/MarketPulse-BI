"""One-time DB fix for the FinBERT sentiment-sign bug (see
src/marketpulse/sentiment/finbert.py): NewsArticles.SentimentScore for
Negative-category articles was stored as a positive confidence value
instead of a negative one, which meant negative news was silently pushing
FactSentiment.AvgSentimentScore *up* instead of down.

The code is already fixed at the source (finbert.classify() now negates the
Negative case). This script corrects the data already sitting in the DB:

  1. Negates SentimentScore for any Negative-category article still stored
     positive. Idempotent: only touches rows where SentimentScore > 0, so
     re-running is safe.
  2. Recomputes FactSentiment.AvgSentimentScore for every (AssetKey, DateKey)
     from the corrected NewsArticles, via the same aggregate_daily_sentiment()
     the pipeline itself uses - a plain (unweighted) mean, unchanged. Only
     the sign bug is fixed here; the "weighted average" question from the
     spec's data dictionary is a separate follow-up.

Usage: PYTHONPATH=src .venv/bin/python scripts/fix_sentiment_sign_bug.py
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

    with engine.begin() as conn:
        result = conn.execute(
            text(
                """
                UPDATE "NewsArticles"
                SET "SentimentScore" = -"SentimentScore"
                WHERE "SentimentCategory" = 'Negative' AND "SentimentScore" > 0
                """
            )
        )
        print(f"Negated {result.rowcount} Negative-article SentimentScore rows")

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
        if ticker is None:  # orphaned article for a since-removed asset; skip
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
