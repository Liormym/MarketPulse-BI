"""Integration test against the local Postgres instance (spec §12: Confirmed Idempotency).

Requires the dev DB to be up and migrated (see README). Runs inside a transaction
that's rolled back at the end so it never leaves test data behind.
"""
from datetime import date, datetime, timezone

import pytest
from sqlalchemy import text

from marketpulse.extract.news import NewsRecord
from marketpulse.extract.prices import PriceRecord
from marketpulse.load.db import get_engine
from marketpulse.load.upsert import get_asset_key_map, upsert_daily_prices, upsert_news_articles


@pytest.fixture
def db_conn():
    engine = get_engine()
    conn = engine.connect()
    trans = conn.begin()
    yield conn
    trans.rollback()
    conn.close()


def test_upsert_daily_prices_is_idempotent(db_conn):
    asset_keys = get_asset_key_map(db_conn)
    assert "AAPL" in asset_keys, "seed the DB first: python scripts/seed_dimensions.py"

    record = PriceRecord(ticker="AAPL", trade_date=date.today(), close=123.45, volume=1000)

    upsert_daily_prices(db_conn, [record], asset_keys)
    upsert_daily_prices(db_conn, [record], asset_keys)  # re-run on the same data

    date_key = int(date.today().strftime("%Y%m%d"))
    rows = db_conn.execute(
        text(
            'SELECT "Close" FROM "FactDailyPrice" WHERE "AssetKey" = :ak AND "DateKey" = :dk'
        ),
        {"ak": asset_keys["AAPL"], "dk": date_key},
    ).all()

    assert len(rows) == 1
    assert rows[0][0] == 123.45


def test_upsert_daily_prices_updates_on_rerun_with_new_value(db_conn):
    asset_keys = get_asset_key_map(db_conn)
    trade_date = date.today()

    upsert_daily_prices(db_conn, [PriceRecord("AAPL", trade_date, 100.0, 500)], asset_keys)
    upsert_daily_prices(db_conn, [PriceRecord("AAPL", trade_date, 105.0, 600)], asset_keys)

    date_key = int(trade_date.strftime("%Y%m%d"))
    row = db_conn.execute(
        text('SELECT "Close", "Volume" FROM "FactDailyPrice" WHERE "AssetKey" = :ak AND "DateKey" = :dk'),
        {"ak": asset_keys["AAPL"], "dk": date_key},
    ).one()

    assert row[0] == 105.0
    assert row[1] == 600


def test_upsert_news_articles_is_idempotent(db_conn):
    asset_keys = get_asset_key_map(db_conn)
    article = NewsRecord(
        article_id="test-idempotency-article",
        ticker="AAPL",
        published_at=datetime.now(timezone.utc),
        title="Test headline for idempotency",
        source_url="https://example.com/test",
        category="Positive",
        score=0.75,
    )

    upsert_news_articles(db_conn, [article], asset_keys)
    upsert_news_articles(db_conn, [article], asset_keys)

    rows = db_conn.execute(
        text('SELECT COUNT(*) FROM "NewsArticles" WHERE "ArticleID" = :aid'),
        {"aid": "test-idempotency-article"},
    ).scalar()

    assert rows == 1
