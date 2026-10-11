"""Idempotent PostgreSQL upserts. Re-running the pipeline on the same data
never creates duplicate rows, per spec §5 step 7 / §12 acceptance criteria.
"""
import json
from datetime import date, datetime

from sqlalchemy import text
from sqlalchemy.engine import Connection

from ..recent_news import safe_http_url


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
                    ("ArticleID", "AssetKey", "PublishedAt", "Title", "SourceUrl")
                VALUES (:article_id, :asset_key, :published_at, :title, :source_url)
                ON CONFLICT ("ArticleID") DO UPDATE
                    SET "SourceUrl" = COALESCE(EXCLUDED."SourceUrl", "NewsArticles"."SourceUrl")
                """
            ),
            {
                "article_id": a.article_id,
                "asset_key": asset_key,
                "published_at": a.published_at,
                "title": a.title,
                "source_url": safe_http_url(getattr(a, "source_url", None)),
            },
        )
        count += 1
    return count


def upsert_investment_score_history(conn: Connection, rows: list[dict]) -> int:
    """Writes one FactInvestmentScoreHistory row per (asset, price bar), as a
    score-v2 row. Re-scoring the same bar (a weekend or holiday re-run, or a
    retry) rewrites that row rather than adding a duplicate; if the bar still
    holds a v1 (sentiment-based) row it is replaced by the v2 one and the v1-only
    columns are cleared, so a row never mixes versions. Each row needs:
    asset_key, date_key, score, trend_points, momentum_points, sector_points,
    risk_points, insider_points, rsi14, close_price, short_percent_of_float, flags (dict),
    computed_at."""
    if not rows:
        return 0
    payload = [{**row, "flags": json.dumps(row["flags"], sort_keys=True)} for row in rows]
    conn.execute(
        text(
            """
            INSERT INTO "FactInvestmentScoreHistory"
                ("AssetKey", "DateKey", "Score", "ScoreVersion", "TrendPoints", "MomentumPoints",
                 "SectorPoints", "RiskPoints", "InsiderPoints", "RSI14", "ClosePrice", "ShortPercentOfFloat", "Flags", "ComputedAt")
            VALUES
                (:asset_key, :date_key, :score, 2, :trend_points, :momentum_points,
                 :sector_points, :risk_points, :insider_points, :rsi14, :close_price, :short_percent_of_float,
                 CAST(:flags AS JSONB), :computed_at)
            ON CONFLICT ("AssetKey", "DateKey") DO UPDATE
                SET "Score" = EXCLUDED."Score",
                    "ScoreVersion" = 2,
                    "TrendPoints" = EXCLUDED."TrendPoints",
                    "MomentumPoints" = EXCLUDED."MomentumPoints",
                    "SectorPoints" = EXCLUDED."SectorPoints",
                    "RiskPoints" = EXCLUDED."RiskPoints",
                    "InsiderPoints" = EXCLUDED."InsiderPoints",
                    "RSI14" = EXCLUDED."RSI14",
                    "SentimentPoints" = NULL,
                    "TechnicalPoints" = NULL,
                    "PositioningPoints" = NULL,
                    "RiskModifierPoints" = NULL,
                    "HasSentiment" = NULL,
                    "ClosePrice" = EXCLUDED."ClosePrice",
                    "ShortPercentOfFloat" = EXCLUDED."ShortPercentOfFloat",
                    "Flags" = EXCLUDED."Flags",
                    "ComputedAt" = EXCLUDED."ComputedAt"
            """
        ),
        payload,
    )
    return len(rows)
