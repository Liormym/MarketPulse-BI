import pandas as pd

from marketpulse.extract import prices as prices_module
from marketpulse.extract.prices import fetch_prices


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
    monkeypatch.setattr(prices_module.time, "sleep", lambda *_: None)

    fetch_prices(["AAPL"], lookback_days=5, period="max")

    assert captured == ["max"]


def test_lookback_days_used_when_no_period_given(monkeypatch):
    captured = []
    monkeypatch.setattr(prices_module.yf, "Ticker", lambda t: _FakeTicker(captured))
    monkeypatch.setattr(prices_module.time, "sleep", lambda *_: None)

    fetch_prices(["AAPL"], lookback_days=5)

    assert captured == ["5d"]


def test_fetch_prices_captures_ohlcv(monkeypatch):
    monkeypatch.setattr(prices_module.yf, "Ticker", lambda t: _FakeTicker([]))
    monkeypatch.setattr(prices_module.time, "sleep", lambda *_: None)

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
