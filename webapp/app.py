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
from marketpulse.scoring import SENTIMENT_LOOKBACK_DAYS, compute_investment_score  # noqa: E402
from rate_limit import check_and_record_refresh  # noqa: E402

app = Flask(__name__)
# Regenerated on each process start - fine for a local, single-operator dev
# tool; it just means refresh-rate-limit history resets on restart too.
app.secret_key = os.environ.get("FLASK_SECRET_KEY") or os.urandom(24)


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
    has_recent_executive_sale = bool(enrichment and enrichment["has_recent_executive_sale"])

    breakdown = compute_investment_score(
        closes=data["prices"],
        volumes=data["volumes"],
        sma20_series=data["sma20"],
        sma50_series=data["sma50"],
        sma200_series=data["sma200"],
        recent_sentiment=data["sentiment"][-SENTIMENT_LOOKBACK_DAYS:],
        atr14=data["atr14"],
        avg_volume_20d=data["avg_volume_20d"],
        sector_flow_status=sector_flow["flow_status"] if sector_flow else None,
        short_percent_of_float=enrichment["short_percent_of_float"] if enrichment else None,
        has_recent_executive_sale=has_recent_executive_sale,
    )
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
