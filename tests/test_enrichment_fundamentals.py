"""Fundamentals (trailing P/E, market cap, beta) in the enrichment layer.
yfinance is replaced with a stub; the DB part runs in a rolled-back transaction
against the local Postgres (migration 007 must be applied)."""
import sys
from pathlib import Path

import pytest
from sqlalchemy import text

from marketpulse.load.db import get_engine

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "webapp") not in sys.path:
    sys.path.insert(0, str(ROOT / "webapp"))

import enrichment  # noqa: E402


class _StubTicker:
    def __init__(self, info=None, raises=False):
        self._info, self._raises = info or {}, raises

    @property
    def info(self):
        if self._raises:
            raise ConnectionError("yahoo unreachable")
        return self._info

    insider_transactions = None


def _patch(monkeypatch, **kwargs):
    stub = _StubTicker(**kwargs)
    monkeypatch.setattr(enrichment.yf, "Ticker", lambda _t: stub)


def test_snapshot_reads_all_four_fields_from_one_info_call(monkeypatch):
    _patch(monkeypatch, info={"trailingPE": 38.2, "marketCap": 4.8e12, "beta": 1.07, "shortPercentOfFloat": 0.0088})
    snap = enrichment._fetch_info_snapshot("AAPL")
    assert snap == {"short_percent_of_float": 0.0088, "trailing_pe": 38.2, "market_cap": 4_800_000_000_000, "beta": 1.07}


def test_loss_making_or_non_equity_instruments_have_no_pe_but_still_succeed(monkeypatch):
    _patch(monkeypatch, info={"marketCap": 1.6e9, "beta": 2.46})  # no trailingPE key at all
    snap = enrichment._fetch_info_snapshot("FCEL")
    assert snap is not None and snap["trailing_pe"] is None and snap["market_cap"] == 1_600_000_000


@pytest.mark.parametrize("bad", ["Infinity", float("inf"), float("nan"), "n/a", None, -5.0, 0])
def test_unusable_pe_values_become_none(monkeypatch, bad):
    _patch(monkeypatch, info={"trailingPE": bad})
    assert enrichment._fetch_info_snapshot("X")["trailing_pe"] is None


def test_beta_may_be_negative_but_not_infinite(monkeypatch):
    _patch(monkeypatch, info={"beta": -0.4})
    assert enrichment._fetch_info_snapshot("X")["beta"] == -0.4
    _patch(monkeypatch, info={"beta": "Infinity"})
    assert enrichment._fetch_info_snapshot("X")["beta"] is None


def test_a_failed_info_call_is_reported_as_failure_not_as_blanks(monkeypatch):
    _patch(monkeypatch, raises=True)
    assert enrichment._fetch_info_snapshot("AAPL") is None


@pytest.fixture
def db_conn():
    conn = get_engine().connect()
    trans = conn.begin()
    yield conn
    trans.rollback()
    conn.close()


def _asset(conn):
    return conn.execute(text('SELECT "AssetKey", "Ticker" FROM "DimAsset" ORDER BY "AssetKey" LIMIT 1')).one()


def test_fetch_stores_fundamentals_and_marks_them_fetched(monkeypatch, db_conn):
    asset_key, ticker = _asset(db_conn)
    db_conn.execute(text('DELETE FROM "StockEnrichmentCache" WHERE "AssetKey" = :a'), {"a": asset_key})
    _patch(monkeypatch, info={"trailingPE": 20.5, "marketCap": 9e9, "beta": 1.3, "shortPercentOfFloat": 0.05})

    result = enrichment.get_or_fetch_enrichment(db_conn, asset_key, ticker)

    assert result["fundamentals"]["trailing_pe"] == 20.5
    assert result["fundamentals"]["market_cap"] == 9_000_000_000
    assert result["fundamentals"]["beta"] == 1.3
    assert result["fundamentals"]["as_of"] is not None
    row = db_conn.execute(
        text('SELECT "TrailingPE", "MarketCap", "Beta", "FundamentalsFetchedAt" FROM "StockEnrichmentCache" WHERE "AssetKey" = :a'),
        {"a": asset_key},
    ).one()
    assert row[0] == 20.5 and row[1] == 9_000_000_000 and row[2] == 1.3 and row[3] is not None


def test_a_cache_row_from_before_the_columns_existed_is_refetched(monkeypatch, db_conn):
    asset_key, ticker = _asset(db_conn)
    db_conn.execute(text('DELETE FROM "StockEnrichmentCache" WHERE "AssetKey" = :a'), {"a": asset_key})
    db_conn.execute(  # fresh FetchedAt, but no fundamentals marker
        text('INSERT INTO "StockEnrichmentCache" ("AssetKey", "ShortPercentOfFloat", "FetchedAt") VALUES (:a, 0.02, now())'),
        {"a": asset_key},
    )
    _patch(monkeypatch, info={"trailingPE": 12.0, "marketCap": 5e9, "beta": 0.9, "shortPercentOfFloat": 0.03})

    result = enrichment.get_or_fetch_enrichment(db_conn, asset_key, ticker)

    assert result["fundamentals"]["trailing_pe"] == 12.0
    assert result["short_percent_of_float"] == 0.03


def test_fresh_cache_is_served_without_calling_yahoo(monkeypatch, db_conn):
    asset_key, ticker = _asset(db_conn)
    db_conn.execute(text('DELETE FROM "StockEnrichmentCache" WHERE "AssetKey" = :a'), {"a": asset_key})
    db_conn.execute(
        text(
            """INSERT INTO "StockEnrichmentCache"
               ("AssetKey", "ShortPercentOfFloat", "TrailingPE", "MarketCap", "Beta", "FetchedAt", "FundamentalsFetchedAt")
               VALUES (:a, 0.02, 18.0, 7000000000, 1.1, now(), now())"""
        ),
        {"a": asset_key},
    )
    _patch(monkeypatch, raises=True)  # would blow up if the cache were bypassed

    result = enrichment.get_or_fetch_enrichment(db_conn, asset_key, ticker)

    assert result["fundamentals"]["trailing_pe"] == 18.0 and result["fundamentals"]["market_cap"] == 7_000_000_000


def test_a_failed_refresh_keeps_the_previously_cached_values(monkeypatch, db_conn):
    asset_key, ticker = _asset(db_conn)
    db_conn.execute(text('DELETE FROM "StockEnrichmentCache" WHERE "AssetKey" = :a'), {"a": asset_key})
    db_conn.execute(
        text(
            """INSERT INTO "StockEnrichmentCache"
               ("AssetKey", "ShortPercentOfFloat", "TrailingPE", "MarketCap", "Beta", "FetchedAt", "FundamentalsFetchedAt")
               VALUES (:a, 0.02, 18.0, 7000000000, 1.1, now() - interval '3 days', now() - interval '3 days')"""
        ),
        {"a": asset_key},
    )
    _patch(monkeypatch, raises=True)

    result = enrichment.get_or_fetch_enrichment(db_conn, asset_key, ticker)

    assert result["fundamentals"]["trailing_pe"] == 18.0
    assert result["short_percent_of_float"] == 0.02
