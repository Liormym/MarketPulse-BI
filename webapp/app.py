"""MarketPulse BI — Flask front-end. Two screens:

  /              Market Risk & Macro Dashboard (yields, oil, sector flows, top movers)
  /stock/<ticker>  Stock deep-dive (price/sentiment chart, technicals, positioning, macro alignment)

The Investment Score (marketpulse.scoring) is a fully explainable, rule-based
score - see that module for the point budget and every rule it applies.
"""
import sys
from pathlib import Path

SRC_PATH = str(Path(__file__).resolve().parents[1] / "src")
if SRC_PATH not in sys.path:
    sys.path.insert(0, SRC_PATH)

from flask import Flask, jsonify, render_template  # noqa: E402

import db  # noqa: E402
from marketpulse.scoring import SENTIMENT_LOOKBACK_DAYS, compute_investment_score  # noqa: E402

app = Flask(__name__)


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

    return jsonify(data)


if __name__ == "__main__":
    app.run(debug=True, port=5050)
