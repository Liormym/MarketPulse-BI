from datetime import datetime, timedelta, timezone

import pytest

from marketpulse.extract import news as news_module
from marketpulse.extract.ratelimit import SharedRateLimiter
from marketpulse.news_tiers import parse_news_tiers, select_news_due

NOW = datetime(2026, 11, 4, 12, 0, tzinfo=timezone.utc)
TIERS = {"sp500": 0.0, "personal": 0.0, "sp400": 24.0, "sp600": 48.0, "tase": 72.0}


def _a(ticker, universe):
    return {"ticker": ticker, "company_name": ticker, "universe": universe}


def test_parse_news_tiers():
    assert parse_news_tiers("sp500:0, sp400:24,tase:72") == {"sp500": 0.0, "sp400": 24.0, "tase": 72.0}
    with pytest.raises(ValueError):
        parse_news_tiers("sp500")
    with pytest.raises(ValueError):
        parse_news_tiers("sp500:-1")


def test_core_universes_are_fetched_every_run_whatever_their_age():
    assets = [_a("AAPL", "sp500"), _a("MINE", "personal")]
    due, c = select_news_due(assets, {"AAPL": NOW, "MINE": NOW}, NOW, TIERS, catchup_budget=0)
    assert [a["ticker"] for a in due] == ["AAPL", "MINE"] and c["core"] == 2


def test_catchup_tiers_wait_for_their_max_age():
    assets = [_a("A", "sp400"), _a("B", "sp400"), _a("C", "tase")]
    fetched = {"A": NOW - timedelta(hours=10), "B": NOW - timedelta(hours=25), "C": NOW - timedelta(hours=71)}
    due, c = select_news_due(assets, fetched, NOW, TIERS, catchup_budget=10)
    assert [a["ticker"] for a in due] == ["B"] and c["not_due"] == 2


def test_never_fetched_come_first_then_most_overdue_and_the_budget_caps_them():
    assets = [_a("OLD", "sp400"), _a("NEW1", "sp600"), _a("NEW2", "tase"), _a("OLDER", "sp400")]
    fetched = {"OLD": NOW - timedelta(hours=30), "OLDER": NOW - timedelta(hours=90)}
    due, c = select_news_due(assets, fetched, NOW, TIERS, catchup_budget=3)
    assert [a["ticker"] for a in due] == ["NEW1", "NEW2", "OLDER"]  # never-fetched (by ticker), then most overdue
    assert c["catchup"] == 3 and c["deferred"] == 1


def test_core_is_never_starved_by_the_catchup_budget_and_untiered_universes_are_skipped():
    assets = [_a("AAPL", "sp500"), _a("SPY", "etf"), _a("BTC", "crypto"), _a("X", "sp600")]
    due, c = select_news_due(assets, {}, NOW, TIERS, catchup_budget=0)
    assert [a["ticker"] for a in due] == ["AAPL"] and c["untiered"] == 2 and c["deferred"] == 1


class _Resp:
    def __init__(self, status):
        self.status_code = status
        self.content = b""

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests

            raise requests.HTTPError(str(self.status_code))


def test_a_429_triggers_the_shared_backoff(monkeypatch):
    limiter = SharedRateLimiter(0)
    monkeypatch.setattr(news_module, "OUTBOUND_LIMITER", limiter)
    monkeypatch.setattr(news_module.requests, "get", lambda *a, **k: _Resp(429))
    monkeypatch.setattr(news_module._parse_feed.retry, "wait", lambda *_: 0)  # no real sleeping between attempts
    monkeypatch.setattr(news_module._parse_feed.retry, "stop", lambda *_: True)

    with pytest.raises(Exception):
        news_module._parse_feed("https://feed.test/x")

    assert limiter.bursts == 1


def test_circuit_breaker_stops_starting_new_tickers_after_repeated_throttling(monkeypatch):
    limiter = SharedRateLimiter(0)
    monkeypatch.setattr(news_module, "OUTBOUND_LIMITER", limiter)
    monkeypatch.setattr(news_module.settings, "news_throttle_abort_after", 2)
    monkeypatch.setattr(news_module.settings, "news_max_workers", 1)
    calls = []

    def fake(ticker, company, limit):
        calls.append(ticker)
        limiter.bursts += 1  # every ticker "hits" a fresh throttle burst
        return [], False

    monkeypatch.setattr(news_module, "fetch_news_for_ticker_status", fake)
    assets = [{"ticker": f"T{i}", "company_name": "c"} for i in range(6)]
    records, reached = news_module.fetch_news_status(assets)

    assert calls == ["T0", "T1"]  # breaker opened after 2 bursts; T2..T5 never started
    assert records == [] and reached == set()  # not "reached", so they stay due for the next run
