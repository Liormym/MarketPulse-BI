import threading
import time

import pandas as pd
import pytest
from tenacity import wait_none

from marketpulse.extract import prices as prices_module
from marketpulse.extract.prices import fetch_prices
from marketpulse.extract.ratelimit import SharedRateLimiter


@pytest.fixture(autouse=True)
def _no_outbound_spacing(monkeypatch):
    monkeypatch.setattr(prices_module, "OUTBOUND_LIMITER", SharedRateLimiter(0))


@pytest.fixture
def _no_retry_backoff(monkeypatch):
    monkeypatch.setattr(prices_module._fetch_one.retry, "wait", wait_none())


class _FakeTicker:
    def __init__(self, captured_periods):
        self._captured_periods = captured_periods

    def history(self, period, interval, timeout):
        self._captured_periods.append(period)
        idx = pd.to_datetime(["2026-01-01"])
        return pd.DataFrame(
            {"Open": [10.0], "High": [11.0], "Low": [9.0], "Close": [10.5], "Volume": [1000]}, index=idx
        )


def test_period_argument_overrides_lookback_days(monkeypatch):
    captured = []
    monkeypatch.setattr(prices_module.yf, "Ticker", lambda t: _FakeTicker(captured))

    fetch_prices(["AAPL"], lookback_days=5, period="max")

    assert captured == ["max"]


def test_lookback_days_used_when_no_period_given(monkeypatch):
    captured = []
    monkeypatch.setattr(prices_module.yf, "Ticker", lambda t: _FakeTicker(captured))

    fetch_prices(["AAPL"], lookback_days=5)

    assert captured == ["5d"]


def test_fetch_prices_captures_ohlcv(monkeypatch):
    monkeypatch.setattr(prices_module.yf, "Ticker", lambda t: _FakeTicker([]))

    records, failed = fetch_prices(["AAPL"], period="max")

    assert failed == []
    assert len(records) == 1
    r = records[0]
    assert r.ticker == "AAPL"
    assert r.close == 10.5
    assert r.open == 10.0
    assert r.high == 11.0
    assert r.low == 9.0
    assert r.volume == 1000


class _FakeTickerWithZeroVolumeRows:
    """Simulates yfinance returning phantom non-trading-day rows (e.g. a
    TASE Friday) alongside real trading days."""

    def history(self, period, interval, timeout):
        idx = pd.to_datetime(["2026-01-01", "2026-01-02", "2026-01-03"])
        return pd.DataFrame(
            {
                "Open": [10.0, 10.5, 10.5],
                "High": [11.0, 10.5, 11.5],
                "Low": [9.0, 10.5, 10.0],
                "Close": [10.5, 10.5, 11.0],
                "Volume": [1000, 0, 800],  # middle day is a phantom zero-volume row
            },
            index=idx,
        )


def test_fetch_prices_drops_zero_volume_rows(monkeypatch):
    monkeypatch.setattr(prices_module.yf, "Ticker", lambda t: _FakeTickerWithZeroVolumeRows())

    records, failed = fetch_prices(["BEZQ.TA"], period="max")

    assert failed == []
    assert len(records) == 2  # the zero-volume middle row is dropped
    assert all(r.volume > 0 for r in records)
    assert [r.trade_date.isoformat() for r in records] == ["2026-01-01", "2026-01-03"]


class _ExplodingTicker:
    def history(self, period, interval, timeout):
        raise ConnectionError("simulated 429")


def test_failed_ticker_is_reported_and_others_still_return(monkeypatch, _no_retry_backoff):
    def make_ticker(symbol):
        return _ExplodingTicker() if symbol == "BAD" else _FakeTicker([])

    monkeypatch.setattr(prices_module.yf, "Ticker", make_ticker)

    records, failed = fetch_prices(["GOOD", "BAD"], period="max")

    assert failed == ["BAD"]
    assert [r.ticker for r in records] == ["GOOD"]


def test_records_follow_input_ticker_order(monkeypatch):
    monkeypatch.setattr(prices_module.yf, "Ticker", lambda t: _FakeTicker([]))
    tickers = ["ZZZ", "AAA", "MMM", "BBB", "QQQ"]

    records, _ = fetch_prices(tickers, period="max")

    assert [r.ticker for r in records] == tickers


def test_concurrency_never_exceeds_price_max_workers(monkeypatch):
    monkeypatch.setattr(prices_module.settings, "price_max_workers", 3)
    in_flight = 0
    peak = 0
    lock = threading.Lock()

    class _SlowTicker:
        def history(self, period, interval, timeout):
            nonlocal in_flight, peak
            with lock:
                in_flight += 1
                peak = max(peak, in_flight)
            time.sleep(0.02)
            with lock:
                in_flight -= 1
            return pd.DataFrame(
                {"Open": [1.0], "High": [1.0], "Low": [1.0], "Close": [1.0], "Volume": [1]},
                index=pd.to_datetime(["2026-01-01"]),
            )

    monkeypatch.setattr(prices_module.yf, "Ticker", lambda t: _SlowTicker())

    records, failed = fetch_prices([f"T{i}" for i in range(12)], period="max")

    assert failed == []
    assert len(records) == 12
    assert 1 < peak <= 3
