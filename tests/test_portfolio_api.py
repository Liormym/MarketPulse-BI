"""Portfolio tracker API: /api/fx, POST /api/portfolio/quotes and the /portfolio shell.
The server is strictly stateless about holdings: it never receives or stores quantities
or buy prices."""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "webapp") not in sys.path:
    sys.path.insert(0, str(ROOT / "webapp"))

import app as flask_app  # noqa: E402
import db  # noqa: E402


@pytest.fixture
def client():
    return flask_app.app.test_client()


# ---- validation (no database needed) ----
@pytest.mark.parametrize(
    "body",
    [None, [], "AAPL", {}, {"tickers": "AAPL"}, {"tickers": [1, 2]}, {"tickers": ["AAPL", None]}],
)
def test_quotes_rejects_malformed_bodies(client, body):
    resp = client.post("/api/portfolio/quotes", json=body)
    assert resp.status_code == 400 and "error" in resp.get_json()


def test_quotes_rejects_non_json(client):
    assert client.post("/api/portfolio/quotes", data="AAPL", content_type="text/plain").status_code == 400


def test_quotes_rejects_bad_ticker_syntax_and_oversized_lists(client):
    assert client.post("/api/portfolio/quotes", json={"tickers": ["AAPL", "A B"]}).status_code == 400
    assert client.post("/api/portfolio/quotes", json={"tickers": ["X'; DROP TABLE"]}).status_code == 400
    too_many = [f"T{i}" for i in range(flask_app.PORTFOLIO_MAX_TICKERS + 1)]
    assert client.post("/api/portfolio/quotes", json={"tickers": too_many}).status_code == 400


def test_quotes_is_post_only(client):
    assert client.get("/api/portfolio/quotes").status_code == 405


def test_quotes_normalises_and_dedupes_and_passes_only_tickers_to_the_db(client, monkeypatch):
    seen = {}

    def fake(tickers):
        seen["tickers"] = tickers
        return {"quotes": {}, "unknown": tickers}

    monkeypatch.setattr(db, "get_portfolio_quotes", fake)
    resp = client.post(
        "/api/portfolio/quotes",
        # extra fields (what a careless client might send) are ignored, never stored or echoed
        json={"tickers": [" aapl", "AAPL", "bezq.ta"], "quantity": 10, "avg_price": 150.0, "holdings": [{"ticker": "AAPL", "quantity": 99}]},
    )
    assert resp.status_code == 200
    assert seen["tickers"] == ["AAPL", "BEZQ.TA"]
    assert "99" not in resp.get_data(as_text=True) and "avg_price" not in resp.get_data(as_text=True)


def test_empty_ticker_list_is_fine(client):
    resp = client.post("/api/portfolio/quotes", json={"tickers": []})
    assert resp.status_code == 200 and resp.get_json() == {"quotes": {}, "unknown": []}


def test_fx_503_when_no_rate_stored(client, monkeypatch):
    monkeypatch.setattr(db, "get_fx_latest", lambda base="USD", quote="ILS": None)
    assert client.get("/api/fx").status_code == 503


def test_portfolio_page_is_a_static_shell(client, monkeypatch):
    monkeypatch.setattr(db, "get_tickers", lambda: [{"ticker": "AAPL", "name": "Apple Inc.", "sector": "Technology", "universe": "sp500", "currency": "USD"}])
    html = client.get("/portfolio").get_data(as_text=True)
    assert 'id="pf-list"' in html and "js/portfolio.js" in html
    assert 'value="AAPL"' in html  # the ticker list feeding the auto-complete
    assert "today's exchange rate, not the historical rate at purchase" in html
    assert 'href="/portfolio"' in html


# ---- against the real database ----
@pytest.mark.populated_db
def test_fx_returns_the_latest_usd_ils_rate_with_freshness(client):
    data = client.get("/api/fx").get_json()
    assert data["base"] == "USD" and data["quote"] == "ILS"
    assert 2.0 < data["rate"] < 6.0
    assert {"as_of", "fetched_at", "age_days", "stale"} <= set(data)


@pytest.mark.populated_db
def test_quotes_for_a_us_and_a_tase_ticker(client):
    data = client.post("/api/portfolio/quotes", json={"tickers": ["AAPL", "BEZQ.TA", "NOPE123"]}).get_json()
    assert data["unknown"] == ["NOPE123"]
    aapl, bezq = data["quotes"]["AAPL"], data["quotes"]["BEZQ.TA"]
    assert aapl["currency"] == "USD" and bezq["currency"] == "ILS"
    for q in (aapl, bezq):
        assert q["price"] > 0 and q["previous_close"] > 0 and q["name"]
        assert q["price_date"] > q["previous_close_date"]
        assert 0 <= q["score"] <= 100 and q["score"] == round(q["score_exact"], 1)
        assert {"price_date", "prices_fetched_at", "price_age_days", "price_stale", "score_computed_at"} <= set(q["freshness"])
    # the score is the same cached v2 score the dashboard's Top 5 shows
    top = db.get_top_strong_buys(limit=1, market="us")[0]
    got = client.post("/api/portfolio/quotes", json={"tickers": [top["ticker"]]}).get_json()["quotes"][top["ticker"]]
    assert got["score_exact"] == top["score_exact"] and got["score"] == top["score"]
