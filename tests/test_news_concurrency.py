import threading
import time

from marketpulse.extract import news as news_module
from marketpulse.extract.news import NewsRecord, fetch_news
from marketpulse.extract.ratelimit import SharedRateLimiter
from datetime import datetime, timezone


def _record(ticker):
    return NewsRecord(
        article_id=f"id-{ticker}",
        ticker=ticker,
        published_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        title=f"headline for {ticker}",
        source_url=f"https://example.test/{ticker}",
    )


def _assets(n):
    return [{"ticker": f"T{i:03d}", "company_name": f"Co {i}"} for i in range(n)]


def test_results_follow_asset_order(monkeypatch):
    monkeypatch.setattr(news_module, "OUTBOUND_LIMITER", SharedRateLimiter(0))
    monkeypatch.setattr(
        news_module,
        "fetch_news_for_ticker",
        lambda ticker, company, limit: [_record(ticker)],
    )
    assets = _assets(10)

    records = fetch_news(assets)

    assert [r.ticker for r in records] == [a["ticker"] for a in assets]


def test_concurrency_is_bounded_by_news_max_workers(monkeypatch):
    monkeypatch.setattr(news_module.settings, "news_max_workers", 3)
    monkeypatch.setattr(news_module, "OUTBOUND_LIMITER", SharedRateLimiter(0))
    in_flight = 0
    peak = 0
    lock = threading.Lock()

    def slow_fetch(ticker, company, limit):
        nonlocal in_flight, peak
        with lock:
            in_flight += 1
            peak = max(peak, in_flight)
        time.sleep(0.02)
        with lock:
            in_flight -= 1
        return [_record(ticker)]

    monkeypatch.setattr(news_module, "fetch_news_for_ticker", slow_fetch)

    records = fetch_news(_assets(12))

    assert len(records) == 12
    assert 1 < peak <= 3


def test_one_crashing_ticker_does_not_abort_the_run(monkeypatch):
    monkeypatch.setattr(news_module, "OUTBOUND_LIMITER", SharedRateLimiter(0))

    def flaky_fetch(ticker, company, limit):
        if ticker == "T002":
            raise RuntimeError("feed parser exploded")
        return [_record(ticker)]

    monkeypatch.setattr(news_module, "fetch_news_for_ticker", flaky_fetch)

    records = fetch_news(_assets(5))

    tickers = [r.ticker for r in records]
    assert "T002" not in tickers
    assert tickers == ["T000", "T001", "T003", "T004"]
