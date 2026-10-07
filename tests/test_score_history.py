"""Integration test against the local Postgres (migration 006 must be applied).
Runs inside a transaction that's rolled back, so it never leaves rows behind.
"""
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import text

from marketpulse.load.db import get_engine
from marketpulse.load.upsert import upsert_investment_score_history

ROOT = Path(__file__).resolve().parents[1]
for sub in ("webapp", "scripts"):
    if str(ROOT / sub) not in sys.path:
        sys.path.insert(0, str(ROOT / sub))

from compute_investment_scores import compute_score_for_ticker, compute_score_snapshot  # noqa: E402


@pytest.fixture
def db_conn():
    conn = get_engine().connect()
    trans = conn.begin()
    yield conn
    trans.rollback()
    conn.close()


def _asset_key(conn):
    return conn.execute(text('SELECT "AssetKey" FROM "DimAsset" ORDER BY "AssetKey" LIMIT 1')).scalar()


def _date_keys(conn):
    """The two EARLIEST calendar days in DimDate. Real score history only ever
    lands on recent price bars, so these can never collide with it, and they
    exist whether DimDate was seeded for a year (CI) or decades (dev)."""
    first, second = conn.execute(text('SELECT "DateKey" FROM "DimDate" ORDER BY "DateKey" LIMIT 2')).scalars().all()
    return first, second


def _row(asset_key, date_key, score=60.0, flags=None):
    return {
        "asset_key": asset_key,
        "date_key": date_key,
        "score": score,
        "sentiment_points": None,
        "technical_points": 40.0,
        "positioning_points": 14.3,
        "risk_modifier_points": -10.0,
        "close_price": 123.45,
        "short_percent_of_float": 0.12,
        "has_sentiment": False,
        "flags": flags if flags is not None else {"high_volatility": True},
        "computed_at": datetime(2026, 10, 7, tzinfo=timezone.utc),
    }


def _count(conn, asset_key, date_key=None):
    sql = 'SELECT count(*) FROM "FactInvestmentScoreHistory" WHERE "AssetKey" = :a'
    params = {"a": asset_key}
    if date_key is not None:
        sql += ' AND "DateKey" = :d'
        params["d"] = date_key
    return conn.execute(text(sql), params).scalar()


def test_rescoring_the_same_bar_replaces_the_row_instead_of_duplicating(db_conn):
    asset_key = _asset_key(db_conn)
    date_key, _ = _date_keys(db_conn)
    upsert_investment_score_history(db_conn, [_row(asset_key, date_key, score=60.0)])
    upsert_investment_score_history(db_conn, [_row(asset_key, date_key, score=72.0, flags={"insider_selling": True})])

    assert _count(db_conn, asset_key, date_key) == 1
    score, flags = db_conn.execute(
        text('SELECT "Score", "Flags" FROM "FactInvestmentScoreHistory" WHERE "AssetKey" = :a AND "DateKey" = :d'),
        {"a": asset_key, "d": date_key},
    ).one()
    assert score == 72.0
    assert flags == {"insider_selling": True}


def test_different_bars_accumulate_as_history(db_conn):
    asset_key = _asset_key(db_conn)
    first, second = _date_keys(db_conn)
    before = _count(db_conn, asset_key)
    upsert_investment_score_history(db_conn, [_row(asset_key, first), _row(asset_key, second, score=55.0)])
    assert _count(db_conn, asset_key) == before + 2


def test_empty_batch_is_a_noop(db_conn):
    assert upsert_investment_score_history(db_conn, []) == 0


@pytest.mark.populated_db
def test_snapshot_agrees_with_the_unchanged_score_function_and_carries_history_fields():
    snapshot = compute_score_snapshot("AAPL")

    assert snapshot is not None
    assert snapshot["breakdown"].score == compute_score_for_ticker("AAPL").score
    assert 19900101 < snapshot["date_key"] < 21000101
    assert snapshot["close_price"] > 0
    assert isinstance(snapshot["has_sentiment"], bool)
