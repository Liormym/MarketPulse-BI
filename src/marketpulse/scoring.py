"""Investment Score: blends a price-trend signal (SMA-150 / SMA-200), a
momentum signal (RSI-14), and a sentiment signal (FactSentiment) into a
single 0-100 score.

Uses standard textbook indicator lengths rather than shrinking them to fit
whatever history happens to be available — see scripts/backfill_historical_prices.py,
which exists precisely so 200 trading days of price are on hand. If there
isn't enough price history yet, the score is None rather than a number quietly
computed on too little data.

Weights and the trend/RSI calibration below are a starting heuristic, not a
research-backed model — they're named constants specifically so they're easy
to revisit once there's real backtesting to point at.
"""
from dataclasses import dataclass

# Must sum to 1.0 — see compute_investment_score() for what happens when a
# component is unavailable (e.g. no sentiment data yet).
WEIGHT_TREND = 0.40
WEIGHT_MOMENTUM = 0.30
WEIGHT_SENTIMENT = 0.30

RSI_PERIOD = 14
SMA_SHORT_PERIOD = 150
SMA_LONG_PERIOD = 200
SENTIMENT_LOOKBACK_DAYS = 14

MIN_PRICES_FOR_SCORE = SMA_LONG_PERIOD


@dataclass
class ScoreBreakdown:
    score: float | None
    trend_score: float | None
    momentum_score: float | None
    sentiment_score: float | None
    reason: str | None = None


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _sma(closes: list[float], period: int) -> float | None:
    if len(closes) < period:
        return None
    return sum(closes[-period:]) / period


def _rsi(closes: list[float], period: int = RSI_PERIOD) -> float | None:
    """Wilder-style RSI over the most recent `period` daily changes."""
    if len(closes) < period + 1:
        return None
    deltas = [closes[i] - closes[i - 1] for i in range(len(closes) - period, len(closes))]
    avg_gain = sum(d for d in deltas if d > 0) / period
    avg_loss = sum(-d for d in deltas if d < 0) / period
    if avg_loss == 0:
        return 100.0
    if avg_gain == 0:
        return 0.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def _trend_score(closes: list[float]) -> float | None:
    sma_short = _sma(closes, SMA_SHORT_PERIOD)
    sma_long = _sma(closes, SMA_LONG_PERIOD)
    if sma_short is None or sma_long is None:
        return None

    price = closes[-1]
    pct_above_long = (price - sma_long) / sma_long
    base = 50 + pct_above_long * 200  # +/-25% vs SMA-200 spans the full 0-100 range
    confirmation = 10 if sma_short > sma_long else -10  # golden vs. death cross
    return _clamp(base + confirmation, 0, 100)


def _sentiment_score(recent_sentiment: list[float | None]) -> float | None:
    values = [v for v in recent_sentiment if v is not None]
    if not values:
        return None
    avg = sum(values) / len(values)  # avg is in [-1, 1]
    return _clamp((avg + 1) * 50, 0, 100)


def compute_investment_score(
    closes: list[float], recent_sentiment: list[float | None]
) -> ScoreBreakdown:
    """`closes` must be ordered oldest-to-newest. `recent_sentiment` should be
    just the trailing SENTIMENT_LOOKBACK_DAYS window (None entries — days with
    no news — are filtered out automatically).
    """
    if len(closes) < MIN_PRICES_FOR_SCORE:
        return ScoreBreakdown(
            score=None,
            trend_score=None,
            momentum_score=None,
            sentiment_score=None,
            reason=(
                f"needs {MIN_PRICES_FOR_SCORE} trading days of price history, "
                f"has {len(closes)}"
            ),
        )

    trend = _trend_score(closes)
    momentum = _rsi(closes)
    sentiment = _sentiment_score(recent_sentiment)

    components = [
        (trend, WEIGHT_TREND),
        (momentum, WEIGHT_MOMENTUM),
        (sentiment, WEIGHT_SENTIMENT),
    ]
    available = [(v, w) for v, w in components if v is not None]
    if not available:
        return ScoreBreakdown(None, trend, momentum, sentiment, reason="no components available")

    total_weight = sum(w for _, w in available)
    score = sum(v * w for v, w in available) / total_weight
    return ScoreBreakdown(round(score), trend, momentum, sentiment)
