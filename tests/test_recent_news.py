"""Recent Company News: strict mention filter, recency order, URL safety, DB refresh."""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from marketpulse.load.db import get_engine
from marketpulse.load.upsert import get_asset_key_map
from marketpulse.recent_news import refresh_recent_news, safe_http_url, select_recent
from marketpulse.news_relevance import mentions_stock

NOW = datetime(2026, 10, 7, 12, tzinfo=timezone.utc)


def _c(title, hours_ago=1):
    return {"article_id": title, "title": title, "published_at": NOW - timedelta(hours=hours_ago)}


def _sel(cands, limit=4):
    return select_recent(cands, "ACME", "Acme Corporation", limit)


def test_newest_first_up_to_four_and_only_headlines_naming_the_company():
    picked = _sel(
        [
            _c("Acme old news", 50),
            _c("Acme newest", 1),
            _c("Nasdaq sinks as tech slides", 0.5),  # market wrap, no mention
            _c("Acme mid", 10),
            _c("Acme second", 5),
            _c("Acme third", 20),
        ]
    )
    assert [p["title"] for p in picked] == ["Acme newest", "Acme second", "Acme mid", "Acme third"]


def test_auto_generated_holdings_notices_do_not_crowd_out_real_news():
    picked = _sel(
        [
            _c("Acme Corporation $ACME Shares Bought by Big Fund LLC - MarketBeat", 1),
            _c("Acme Corporation Stock Position Raised by Other Capital Inc.", 1),
            _c("Big Fund Acquires New Stake in Acme", 1),
            _c("Acme unveils new product line", 5),
        ]
    )
    assert [p["title"] for p in picked] == ["Acme unveils new product line"]


def test_duplicates_collapse_and_empty_is_fine():
    picked = _sel([_c("Acme Jumps! - Reuters", 5), _c("acme jumps - MSN", 2), _c("Acme other", 1)])
    assert [p["title"] for p in picked] == ["Acme other", "acme jumps - MSN"]
    assert _sel([]) == []


@pytest.mark.parametrize(
    "title,ticker,name,ok",
    [
        ("Chevron CEO warns against export ban", "CVX", "Chevron Corporation", True),
        ("Why Oracle (ORCL) Shares Are Trading Lower Today", "ORCL", "Oracle", True),
        ("Meta stock slides on ad concerns", "META", "Meta Platforms, Inc.", True),
        ("Home Depot slumps on weak housing data", "HD", "Home Depot (The)", True),
        ("AAPL jumps after earnings", "AAPL", "Apple Inc.", True),
        ("Nvidia's AI power problem is its biggest edge", "NVDA", "NVIDIA Corporation", True),
        ("Gartner (IT) raises its outlook", "IT", "Gartner", True),
        ("McDonald's calls the suit filled with inaccuracies", "MCD", "McDonald's", True),  # name contains 's
        ("Could Buying SoundHound AI Stock Today Set You Up for Life?", "NVDA", "NVIDIA Corporation", False),  # the NVDA/SoundHound case
        ("Nasdaq Sinks 345 Points as OpenAI Woes Weigh on Tech", "CVX", "Chevron Corporation", False),
        ("Sector Update: Financial Stocks Advance", "MET", "MetLife", False),
        ("Datadog vs. Dynatrace and Cisco: who wins?", "CSCO", "Cisco", False),  # comparison
        ("Quarterly results roundup - Nvidia Newsroom", "NVDA", "NVIDIA Corporation", False),  # name only in the publisher byline
        ("General Motors recalls 100k trucks", "GIS", "General Mills", False),  # generic first word
        ("Is it time to buy", "IT", "Gartner", False),  # short ticker is an ordinary word
    ],
)
def test_mentions_stock(title, ticker, name, ok):
    assert mentions_stock(title, ticker, name) is ok


@pytest.mark.parametrize(
    "url,ok",
    [
        ("https://finance.yahoo.com/news/x", True),
        ("http://example.com/a?b=1", True),
        ("javascript:alert(1)", False),
        ("data:text/html;base64,AAAA", False),
        ("//evil.example/x", False),
        ("https://", False),
        ("", False),
        (None, False),
        ("https://e.com/" + "a" * 3000, False),
    ],
)
def test_safe_http_url(url, ok):
    assert (safe_http_url(url) is not None) is ok


@pytest.fixture
def db_conn():
    conn = get_engine().connect()
    trans = conn.begin()
    yield conn
    trans.rollback()
    conn.close()


def _insert_article(conn, asset_key, title, url="https://example.com/a", days_ago=1.0):
    conn.execute(
        text(
            """
            INSERT INTO "NewsArticles" ("ArticleID", "AssetKey", "PublishedAt", "Title", "SourceUrl")
            VALUES (:id, :ak, now() - make_interval(secs => :s), :t, :u)
            """
        ),
        {"id": uuid.uuid4().hex, "ak": asset_key, "s": days_ago * 86400, "t": title, "u": url},
    )


def _panel(conn, asset_key):
    return conn.execute(
        text(
            """
            SELECT rn."Rank", n."Title" FROM "StockRecentNews" rn
            JOIN "NewsArticles" n ON n."ArticleID" = rn."ArticleID"
            WHERE rn."AssetKey" = :ak ORDER BY rn."Rank"
            """
        ),
        {"ak": asset_key},
    ).all()


def test_refresh_ranks_by_recency_filters_and_replaces(db_conn):
    ak = get_asset_key_map(db_conn)["AAPL"]
    db_conn.execute(text('DELETE FROM "StockRecentNews" WHERE "AssetKey" = :a'), {"a": ak})
    db_conn.execute(text('UPDATE "NewsArticles" SET "SourceUrl" = NULL WHERE "AssetKey" = :a'), {"a": ak})

    for i, title in enumerate(["Apple zz a", "Apple zz b", "Apple zz c", "Apple zz d", "Apple zz e"]):
        _insert_article(db_conn, ak, title, days_ago=0.1 * (i + 1))
    _insert_article(db_conn, ak, "Samsung zz unrelated", days_ago=0.01)  # newest but not about Apple
    _insert_article(db_conn, ak, "Apple zz old", days_ago=30)  # outside the window
    _insert_article(db_conn, ak, "Apple zz nourl", url=None, days_ago=0.02)  # cannot be linked

    refresh_recent_news(db_conn, [ak])
    assert [t for _, t in _panel(db_conn, ak)] == ["Apple zz a", "Apple zz b", "Apple zz c", "Apple zz d"]

    _insert_article(db_conn, ak, "Apple zz fresh", days_ago=0.001)
    refresh_recent_news(db_conn, [ak])  # replaced, not appended to
    assert [t for _, t in _panel(db_conn, ak)] == ["Apple zz fresh", "Apple zz a", "Apple zz b", "Apple zz c"]


def test_refresh_clears_stale_items_when_nothing_qualifies(db_conn):
    ak = get_asset_key_map(db_conn)["AAPL"]
    db_conn.execute(text('UPDATE "NewsArticles" SET "SourceUrl" = NULL WHERE "AssetKey" = :a'), {"a": ak})
    _insert_article(db_conn, ak, "Apple zz only")
    refresh_recent_news(db_conn, [ak])
    assert len(_panel(db_conn, ak)) == 1

    db_conn.execute(text('UPDATE "NewsArticles" SET "SourceUrl" = NULL WHERE "AssetKey" = :a'), {"a": ak})
    refresh_recent_news(db_conn, [ak])
    assert _panel(db_conn, ak) == []
