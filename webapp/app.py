"""MarketPulse BI — Flask front-end. Two screens:

  /              Market Risk & Macro Dashboard (yields, oil, sector flows, top movers)
  /stock/<ticker>  Stock deep-dive (price/sentiment chart, technicals, positioning, macro alignment)

The Investment Score (marketpulse.scoring) is a fully explainable, rule-based
score - see that module for the point budget and every rule it applies.
Pattern-detection hints (marketpulse.pattern_detection) are surfaced
alongside it but are NOT part of that score - see that module's docstring.
"""
import os
import sys
from pathlib import Path

SRC_PATH = str(Path(__file__).resolve().parents[1] / "src")
if SRC_PATH not in sys.path:
    sys.path.insert(0, SRC_PATH)

from flask import Flask, jsonify, render_template, session  # noqa: E402

import db  # noqa: E402
from marketpulse.live_refresh import refresh_ticker  # noqa: E402
from marketpulse.load.db import get_engine  # noqa: E402
from marketpulse.pattern_detection import detect_patterns  # noqa: E402
from marketpulse.scoring import compute_investment_score  # noqa: E402
from marketpulse.technicals import describe_gap  # noqa: E402
from rate_limit import check_and_record_refresh  # noqa: E402

app = Flask(__name__)
# Regenerated on each process start - fine for a local, single-operator dev
# tool; it just means refresh-rate-limit history resets on restart too.
app.secret_key = os.environ.get("FLASK_SECRET_KEY") or os.urandom(24)


@app.route("/health")
def health():
    """Liveness probe: is the process itself alive? Deliberately does not
    touch the DB - a slow/unreachable Postgres shouldn't make Kubernetes
    conclude the Flask process is dead and restart-loop it. See /health/ready
    for the dependency-aware check."""
    return jsonify({"status": "ok"})


@app.route("/health/ready")
def readiness():
    """Readiness probe: can this pod actually serve traffic right now? Runs
    a trivial query so Kubernetes stops routing to a pod whose DB connection
    is down, without restarting it (that's what /health is for)."""
    try:
        with get_engine().connect() as conn:
            conn.execute(db.text("SELECT 1"))
        return jsonify({"status": "ready"})
    except Exception as exc:
        return jsonify({"status": "not_ready", "error": str(exc)}), 503


@app.route("/")
def dashboard():
    return render_template("dashboard.html")


@app.route("/api/macro")
def api_macro():
    return jsonify(db.get_macro_snapshot() or {})


@app.route("/api/sector-flows")
def api_sector_flows():
    return jsonify(db.get_sector_flows())


@app.route("/api/movers")
def api_movers():
    return jsonify(db.get_top_movers())


@app.route("/api/top-scores")
def api_top_scores():
    return jsonify(db.get_top_strong_buys())


@app.route("/stock/<ticker>")
def stock_detail(ticker):
    tickers = db.get_tickers()
    return render_template("stock_detail.html", tickers=tickers, default_ticker=ticker.upper())


@app.route("/api/tickers")
def api_tickers():
    return jsonify(db.get_tickers())


@app.route("/api/stock/<ticker>")
def api_stock(ticker: str):
    ticker = ticker.upper()
    data = db.get_price_sentiment_history(ticker)
    if data is None:
        return jsonify({"error": f"Unknown ticker '{ticker}'"}), 404

    sector_flow = None
    if data["sector_spdr"]:
        sector_flow = next(
            (s for s in db.get_sector_flows() if s["ticker"] == data["sector_spdr"]), None
        )
    data["sector_flow"] = sector_flow

    enrichment = db.enrich_stock(ticker)
    data["enrichment"] = enrichment

    breakdown = compute_investment_score(**db.build_score_kwargs(data, sector_flow, enrichment))
    data["score"] = breakdown.score
    data["score_detail"] = {
        "sentiment_points": breakdown.sentiment_points,
        "technical_points": breakdown.technical_points,
        "positioning_points": breakdown.positioning_points,
        "risk_modifier_points": breakdown.risk_modifier_points,
        "audit_trail": breakdown.audit_trail,
        "flags": breakdown.flags,
        "reason": breakdown.reason,
    }

    # Heuristic pattern hints - informational only, never fed into the score above.
    data["pattern_hints"] = [
        {"name": h.name, "description": h.description} for h in detect_patterns(data["prices"])
    ]

    # Structured gap info for the latest bar (direction + exact price bounds,
    # not just a bare percentage) - see technicals.describe_gap().
    gap = None
    if len(data["opens"]) >= 1 and len(data["prices"]) >= 2 and data["opens"][-1] is not None:
        gap = describe_gap(data["opens"][-1], data["prices"][-2])
    data["latest_gap"] = (
        {
            "direction": gap.direction,
            "prev_close": round(gap.prev_close, 2),
            "open": round(gap.open, 2),
            "gap_pct": round(gap.gap_pct, 2),
        }
        if gap is not None
        else None
    )

    return jsonify(data)


@app.route("/api/stock/<ticker>/refresh", methods=["POST"])
def api_stock_refresh(ticker: str):
    ticker = ticker.upper()

    allowed, remaining, retry_after = check_and_record_refresh(session)
    if not allowed:
        minutes = max(retry_after // 60, 1)
        return (
            jsonify(
                {
                    "error": "rate_limited",
                    "message": f"Refresh limit reached (5 per hour). Try again in about {minutes} min.",
                    "retry_after_seconds": retry_after,
                }
            ),
            429,
        )

    asset_key = db.get_asset_key(ticker)
    if asset_key is None:
        return jsonify({"error": f"Unknown ticker '{ticker}'"}), 404

    company_name = next((t["name"] for t in db.get_tickers() if t["ticker"] == ticker), ticker)
    result = refresh_ticker(get_engine(), ticker, company_name)

    return jsonify(
        {
            "ok": not result.failed,
            "error": result.error,
            "price_rows": result.price_rows,
            "articles_fetched": result.articles_fetched,
            "technicals_rows": result.technicals_rows,
            "remaining_refreshes": remaining,
        }
    )


if __name__ == "__main__":
    app.run(debug=True, port=5050)
