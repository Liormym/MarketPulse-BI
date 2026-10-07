"""/api/stock/<ticker> exposes RSI and fundamentals without touching the score.
Runs against the local DB through the real Flask route."""
import pytest
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "webapp") not in sys.path:
    sys.path.insert(0, str(ROOT / "webapp"))

import app as flask_app  # noqa: E402
import enrichment  # noqa: E402

TICKER = "AAPL"


def _get():
    resp = flask_app.app.test_client().get(f"/api/stock/{TICKER}")
    assert resp.status_code == 200
    return resp.get_json()


@pytest.mark.populated_db
def test_payload_has_rsi_and_fundamentals_fields():
    data = _get()

    assert {"rsi14", "rsi_state", "rsi_thresholds", "fundamentals"} <= data.keys()
    assert data["rsi_thresholds"] == {"oversold": 30.0, "overbought": 70.0}
    assert set(data["fundamentals"]) == {"trailing_pe", "market_cap", "beta", "as_of"}
    assert "fundamentals" not in data["enrichment"]  # exposed once, at the top level


@pytest.mark.populated_db
def test_rsi_state_is_consistent_with_the_rsi_value():
    data = _get()
    rsi = data["rsi14"]

    if rsi is None:
        assert data["rsi_state"] is None
    else:
        assert 0 <= rsi <= 100
        expected = "oversold" if rsi < 30 else "overbought" if rsi > 70 else "neutral"
        assert data["rsi_state"] == expected


@pytest.mark.populated_db
def test_fundamentals_and_rsi_never_change_the_investment_score(monkeypatch):
    baseline = _get()["score"]

    real = enrichment.get_or_fetch_enrichment

    def tampered(conn, asset_key, ticker):
        result = real(conn, asset_key, ticker)
        result["fundamentals"] = {"trailing_pe": 1.0, "market_cap": 1, "beta": 99.0, "as_of": "2026-01-01T00:00:00+00:00"}
        return result

    monkeypatch.setattr(enrichment, "get_or_fetch_enrichment", tampered)

    assert _get()["score"] == baseline
