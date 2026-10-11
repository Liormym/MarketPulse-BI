"""Recent Company News: the latest headlines that are explicitly about a stock.

Shown on the stock page as plain headlines (link + date). No sentiment, no
scores, no colours: news is context for the reader and is NOT an input to the
Investment Score. A headline qualifies only if it names the company or ticker
(news_relevance.mentions_stock). Newest first, near-duplicate headlines
(the same story via Yahoo and Google) collapsed.

Stored in StockRecentNews (migration 010) as a ranked pointer into
NewsArticles, rewritten per asset on every pipeline / live-refresh run.
"""
from __future__ import annotations

from datetime import datetime, timezone
from urllib.parse import urlsplit

from sqlalchemy import text
from sqlalchemy.engine import Connection

from .news_relevance import is_low_signal, mentions_stock, normalize_title

NEWS_COUNT = 4
NEWS_LOOKBACK_DAYS = 14
_MAX_URL_LENGTH = 2048


def safe_http_url(url: str | None) -> str | None:
    """Only http(s) URLs with a host are ever stored or linked. Feed content
    is untrusted: a `javascript:` or `data:` link must never reach an href."""
    if not url or len(url) > _MAX_URL_LENGTH:
        return None
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return None
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return None
    return url.strip()


def select_recent(
    candidates: list[dict], ticker: str, company_name: str, limit: int = NEWS_COUNT
) -> list[dict]:
    """candidates: dicts with article_id, title, published_at. Newest first."""
    chosen, seen = [], set()
    for c in sorted(candidates, key=lambda c: c["published_at"], reverse=True):
        if not mentions_stock(c["title"], ticker, company_name) or is_low_signal(c["title"]):
            continue
        key = normalize_title(c["title"])
        if not key or key in seen:
            continue
        seen.add(key)
        chosen.append(c)
        if len(chosen) == limit:
            break
    return chosen


def refresh_recent_news(conn: Connection, asset_keys: list[int]) -> int:
    """Recomputes the panel for the given assets from NewsArticles. Assets with
    no qualifying article end up with NO rows (stale items are removed). Caller
    owns the transaction. Returns rows written."""
    now = datetime.now(timezone.utc)
    written = 0
    for asset_key in asset_keys:
        asset = conn.execute(
            text('SELECT "Ticker", "CompanyName" FROM "DimAsset" WHERE "AssetKey" = :a'), {"a": asset_key}
        ).first()
        if asset is None:
            continue
        rows = conn.execute(
            text(
                """
                SELECT "ArticleID", "Title", "PublishedAt"
                FROM "NewsArticles"
                WHERE "AssetKey" = :asset_key
                  AND "SourceUrl" IS NOT NULL
                  AND "PublishedAt" >= now() - make_interval(days => :days)
                ORDER BY "PublishedAt" DESC
                LIMIT 200
                """
            ),
            {"asset_key": asset_key, "days": NEWS_LOOKBACK_DAYS},
        ).mappings().all()
        picked = select_recent(
            [{"article_id": r["ArticleID"], "title": r["Title"], "published_at": r["PublishedAt"]} for r in rows],
            ticker=asset[0],
            company_name=asset[1],
        )
        conn.execute(text('DELETE FROM "StockRecentNews" WHERE "AssetKey" = :a'), {"a": asset_key})
        for rank, c in enumerate(picked, start=1):
            conn.execute(
                text(
                    """
                    INSERT INTO "StockRecentNews" ("AssetKey", "Rank", "ArticleID", "ComputedAt")
                    VALUES (:a, :rank, :article_id, :now)
                    """
                ),
                {"a": asset_key, "rank": rank, "article_id": c["article_id"], "now": now},
            )
            written += 1
    return written
