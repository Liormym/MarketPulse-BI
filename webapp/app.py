"""MarketPulse BI — Flask front-end.

Reads real tickers, prices, and sentiment from PostgreSQL (DimAsset,
FactDailyPrice, FactSentiment, joined on DimDate), and computes the
Investment Score from that same data via marketpulse.scoring.
"""
import sys
from pathlib import Path

SRC_PATH = str(Path(__file__).resolve().parents[1] / "src")
if SRC_PATH not in sys.path:
    sys.path.insert(0, SRC_PATH)

from flask import Flask, jsonify, render_template  # noqa: E402

from db import get_price_sentiment_history, get_tickers  # noqa: E402
from marketpulse.scoring import SENTIMENT_LOOKBACK_DAYS, compute_investment_score  # noqa: E402

app = Flask(__name__)


@app.route("/")
def index():
    tickers = get_tickers()
    default_ticker = tickers[0]["ticker"] if tickers else None
    return render_template("index.html", tickers=tickers, default_ticker=default_ticker)


@app.route("/api/tickers")
def api_tickers():
    return jsonify(get_tickers())


@app.route("/api/stock/<ticker>")
def api_stock(ticker: str):
    data = get_price_sentiment_history(ticker.upper())
    if data is None:
        return jsonify({"error": f"Unknown ticker '{ticker.upper()}'"}), 404

    breakdown = compute_investment_score(
        closes=data["prices"],
        recent_sentiment=data["sentiment"][-SENTIMENT_LOOKBACK_DAYS:],
    )
    data["score"] = breakdown.score
    data["score_detail"] = {
        "trend": breakdown.trend_score,
        "momentum": breakdown.momentum_score,
        "sentiment": breakdown.sentiment_score,
        "reason": breakdown.reason,
    }
    return jsonify(data)


if __name__ == "__main__":
    app.run(debug=True, port=5050)
