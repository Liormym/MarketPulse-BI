"""Integration test against the local Postgres instance: asserts the live
/api/stock/<ticker> route and scripts/compute_investment_scores.py's batch
scorer compute the IDENTICAL Investment Score for the same ticker.

Regression test for a real bug: the batch script used to hand-roll its own
short-interest/insider-sale lookup (cached-only, no yfinance fetch) instead
of calling enrich_stock() like the live route does. A ticker whose
enrichment cache was empty or stale would score with no insider-selling
penalty in the batch path while the live route (which fetches fresh on a
cache miss) applied it - the same ticker showing two different scores
depending on which screen you looked at (dashboard's Top 5 Strong Buys vs.
that ticker's own deep-dive page). Both entry points now share one pipeline
(db.build_score_kwargs) - this test exercises each entry point's REAL code
(the actual Flask route, the actual batch-script function), not two calls
to the same helper, so it would have caught the original bug.

Requires the dev DB to be up and migrated, and AAPL to have
>= MIN_PRICES_FOR_SCORE days of price history (see README).
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "webapp") not in sys.path:
    sys.path.insert(0, str(ROOT / "webapp"))

import app as flask_app  # noqa: E402

sys.path.insert(0, str(ROOT / "scripts"))
from compute_investment_scores import compute_score_for_ticker  # noqa: E402

TICKER = "AAPL"


def test_batch_and_live_paths_compute_the_identical_score_for_the_same_ticker():
    client = flask_app.app.test_client()
    resp = client.get(f"/api/stock/{TICKER}")
    assert resp.status_code == 200, "seed the DB first: python scripts/backfill_historical_prices.py"
    live_payload = resp.get_json()

    batch_breakdown = compute_score_for_ticker(TICKER)
    assert batch_breakdown is not None
    assert batch_breakdown.score is not None

    assert batch_breakdown.score == live_payload["score"]
    assert batch_breakdown.sentiment_points == live_payload["score_detail"]["sentiment_points"]
    assert batch_breakdown.technical_points == live_payload["score_detail"]["technical_points"]
    assert batch_breakdown.positioning_points == live_payload["score_detail"]["positioning_points"]
    assert batch_breakdown.risk_modifier_points == live_payload["score_detail"]["risk_modifier_points"]
