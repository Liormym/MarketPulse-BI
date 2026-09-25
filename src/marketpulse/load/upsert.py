"""Idempotent PostgreSQL upserts. Re-running the pipeline on the same data
never creates duplicate rows, per spec §5 step 7 / §12 acceptance criteria.
"""
from datetime import date, datetime

from sqlalchemy import text
from sqlalchemy.engine import Connection


def get_asset_key_map(conn: Connection) -> dict[str, int]:
    rows = conn.execute(text('SELECT "Ticker", "AssetKey" FROM "DimAsset"')).all()
    return {ticker: key for ticker, key in rows}


def upsert_daily_prices(conn: Connection, records, asset_keys: dict[str, int]) -> int:
    count = 0
    for r in records:
        asset_key = asset_keys.get(r.ticker)
        if asset_key is None:
            continue
        conn.execute(
            text(
                """
                INSERT INTO "FactDailyPrice" ("AssetKey", "DateKey", "Close", "Volume", "Open", "High", "Low")
                VALUES (:asset_key, :date_key, :close, :volume, :open, :high, :low)
                ON CONFLICT ("AssetKey", "DateKey") DO UPDATE
                    SET "Close" = EXCLUDED."Close", "Volume" = EXCLUDED."Volume",
                        "Open" = EXCLUDED."Open", "High" = EXCLUDED."High", "Low" = EXCLUDED."Low"
                """
            ),
            {
                "asset_key": asset_key,
                "date_key": int(r.trade_date.strftime("%Y%m%d")),
                "close": r.close,
                "volume": r.volume,
                "open": getattr(r, "open", None),
                "high": getattr(r, "high", None),
                "low": getattr(r, "low", None),
            },
        )
        count += 1
    return count


def upsert_news_articles(conn: Connection, scored_articles, asset_keys: dict[str, int]) -> int:
    count = 0
    for a in scored_articles:
        asset_key = asset_keys.get(a.ticker)
        if asset_key is None:
            continue
        conn.execute(
            text(
                """
                INSERT INTO "NewsArticles"
                    ("ArticleID", "AssetKey", "PublishedAt", "Title", "SentimentCategory", "SentimentScore")
                VALUES (:article_id, :asset_key, :published_at, :title, :category, :score)
                ON CONFLICT ("ArticleID") DO UPDATE
                    SET "SentimentCategory" = EXCLUDED."SentimentCategory",
                        "SentimentScore" = EXCLUDED."SentimentScore"
                """
            ),
            {
                "article_id": a.article_id,
                "asset_key": asset_key,
                "published_at": a.published_at,
                "title": a.title,
                "category": a.category,
                "score": a.score,
            },
        )
        count += 1
    return count


def upsert_daily_sentiment(conn: Connection, daily_records, asset_keys: dict[str, int]) -> int:
    count = 0
    for d in daily_records:
        asset_key = asset_keys.get(d.ticker)
        if asset_key is None:
            continue
        conn.execute(
            text(
                """
                INSERT INTO "FactSentiment"
                    ("AssetKey", "DateKey", "ArticleCount", "PositiveCount", "NegativeCount", "AvgSentimentScore")
                VALUES (:asset_key, :date_key, :article_count, :positive_count, :negative_count, :avg_score)
                ON CONFLICT ("AssetKey", "DateKey") DO UPDATE
                    SET "ArticleCount" = EXCLUDED."ArticleCount",
                        "PositiveCount" = EXCLUDED."PositiveCount",
                        "NegativeCount" = EXCLUDED."NegativeCount",
                        "AvgSentimentScore" = EXCLUDED."AvgSentimentScore"
                """
            ),
            {
                "asset_key": asset_key,
                "date_key": int(d.trade_date.strftime("%Y%m%d")),
                "article_count": d.article_count,
                "positive_count": d.positive_count,
                "negative_count": d.negative_count,
                "avg_score": d.avg_sentiment_score,
            },
        )
        count += 1
    return count
