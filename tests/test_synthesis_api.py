"""/api/market-brief and the recent_news field of /api/stock/<ticker>."""
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "webapp") not in sys.path:
    sys.path.insert(0, str(ROOT / "webapp"))

import app as flask_app  # noqa: E402


def test_market_brief_endpoint_shape():
    resp = flask_app.app.test_client().get("/api/market-brief")
    assert resp.status_code == 200
    data = resp.get_json()
    if data:  # empty object until the first brief has been generated
        assert {"as_of", "brief", "generated_at"} <= data.keys()
        assert data["brief"].strip()


@pytest.mark.populated_db
def test_stock_payload_exposes_safe_plain_recent_news():
    data = flask_app.app.test_client().get("/api/stock/AAPL").get_json()
    news = data["recent_news"]
    assert len(news) <= 4
    for d in news:
        assert d.keys() == {"title", "source", "url", "published_at"}  # news is plain: no sentiment sign or score
        assert d["url"] is None or d["url"].startswith(("http://", "https://"))


@pytest.mark.populated_db
def test_top_scores_are_ranked_on_the_exact_score_with_one_decimal_display():
    rows = flask_app.app.test_client().get("/api/top-scores").get_json()
    assert len(rows) == 5
    exact = [r["score_exact"] for r in rows]
    assert exact == sorted(exact, reverse=True)  # ranked by the unrounded score
    for r in rows:
        assert r["score"] == round(r["score_exact"], 1)


def test_static_assets_are_content_hashed_so_a_stale_cache_cannot_pair_with_a_new_page():
    html = flask_app.app.test_client().get("/").get_data(as_text=True)
    for asset in ("css/style.css", "js/dashboard.js", "js/freshness.js"):
        assert re.search(rf"/static/{re.escape(asset)}\?v=[0-9a-f]{{10}}", html), asset


def test_market_brief_card_is_hidden_with_the_html_attribute_not_only_css():
    html = flask_app.app.test_client().get("/").get_data(as_text=True)
    assert re.search(r'<section class="market-brief" id="market-brief" hidden', html)
