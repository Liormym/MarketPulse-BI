"""Investment Score: a fully explainable, rule-based 0-100 score combining
sentiment, technical trend/momentum, sector positioning, and risk modifiers.

Every rule that fires appends a human-readable line to `audit_trail` - the
score is never a black box; every point can be traced to a specific,
named rule.

Point budget (sums to exactly 100 when everything is maximally bullish):
    Sentiment              0 to 30
    Technical              0 to 40   (3 base rules + Seller Exhaustion bonus)
    Positioning & Macro  -10 to 30   (Sector Flow + dynamic Short Interest rule)
    Risk Modifiers       -30 to  0   (ATR volatility penalty, insider-selling penalty)
Final score = sum of the above, clamped to [0, 100].

Needs 200 trading days of price (SMA-200) to compute at all - see
scripts/backfill_historical_prices.py. Returns score=None with a reason
otherwise, rather than a number quietly computed on too little data.

All point values/thresholds below are named constants: documented
calibration heuristics, not a backtested model, deliberately easy to retune.
"""
from dataclasses import dataclass, field

SENTIMENT_LOOKBACK_DAYS = 14
MIN_PRICES_FOR_SCORE = 200

# --- Sentiment (0-30 pts) ---
MAX_SENTIMENT_POINTS = 30.0

# --- Technical (0-40 pts) ---
POINTS_PRICE_ABOVE_SMA50 = 10.0
POINTS_PRICE_ABOVE_SMA200 = 15.0
POINTS_SMA20_ABOVE_SMA50 = 10.0
POINTS_SELLER_EXHAUSTION = 5.0
SELLER_EXHAUSTION_LOOKBACK_DAYS = 5  # trading days checked for the down-day/volume pattern

# --- Positioning & Macro (-10 to 30 pts) ---
POINTS_SECTOR_ACCUMULATION = 20.0
POINTS_SECTOR_NEUTRAL = 10.0
POINTS_SECTOR_DISTRIBUTION = 0.0
SHORT_INTEREST_HIGH_THRESHOLD = 0.10  # 10% of float
POINTS_SHORT_SQUEEZE_BONUS = 10.0
POINTS_BEARISH_CONVICTION_PENALTY = -10.0
# "HIGH" technical score for the short-interest rule: >= half of the 40-pt max
TECHNICAL_HIGH_THRESHOLD = 20.0

# --- Risk Modifiers (-30 to 0 pts) ---
ATR_VOLATILITY_PENALTY_THRESHOLD_PCT = 4.5
POINTS_HIGH_VOLATILITY_PENALTY = -10.0
POINTS_INSIDER_SELLING_PENALTY = -20.0
INSIDER_SELLING_LOOKBACK_DAYS = 90  # how far back a CEO/CFO sale still counts as "recent"


@dataclass
class ScoreBreakdown:
    score: float | None
    sentiment_points: float | None
    technical_points: float | None
    positioning_points: float | None
    risk_modifier_points: float | None
    audit_trail: list[str] = field(default_factory=list)
    flags: dict = field(default_factory=dict)
    reason: str | None = None


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _last_non_null(values: list[float | None]) -> float | None:
    for v in reversed(values):
        if v is not None:
            return v
    return None


def _weighted_recent_sentiment(recent_sentiment: list[float | None]) -> float | None:
    """Plain mean across the trailing daily (already confidence-weighted -
    see aggregate.py) sentiment scores. None if no scored days in the window."""
    values = [v for v in recent_sentiment if v is not None]
    if not values:
        return None
    return sum(values) / len(values)


def _sentiment_points(weighted_sentiment: float | None, audit: list[str]) -> float:
    if weighted_sentiment is None:
        audit.append("+0.0 pts: No recent sentiment data available")
        return 0.0
    points = _clamp((weighted_sentiment + 1) / 2 * MAX_SENTIMENT_POINTS, 0, MAX_SENTIMENT_POINTS)
    audit.append(
        f"+{points:.1f} pts: Weighted sentiment {weighted_sentiment:+.2f} "
        f"(confidence-weighted avg, last {SENTIMENT_LOOKBACK_DAYS}d)"
    )
    return points


def _seller_exhaustion(recent_closes: list[float], recent_volumes: list[int], avg_volume_20d: float | None) -> bool:
    """True if every down-day in the recent window happened on below-average
    volume - price falling without volume conviction, a classic bullish-
    reversal tell."""
    if avg_volume_20d is None or len(recent_closes) < 2:
        return False
    down_day_volumes = [
        recent_volumes[i] for i in range(1, len(recent_closes)) if recent_closes[i] < recent_closes[i - 1]
    ]
    if not down_day_volumes:
        return False
    return all(v < avg_volume_20d for v in down_day_volumes)


def _technical_points(
    price: float,
    sma20: float | None,
    sma50: float | None,
    sma200: float | None,
    recent_closes: list[float],
    recent_volumes: list[int],
    avg_volume_20d: float | None,
    audit: list[str],
    flags: dict,
) -> float:
    points = 0.0
    if sma50 is not None and price > sma50:
        points += POINTS_PRICE_ABOVE_SMA50
        audit.append(f"+{POINTS_PRICE_ABOVE_SMA50:.0f} pts: Price above 50-day SMA")
    if sma200 is not None and price > sma200:
        points += POINTS_PRICE_ABOVE_SMA200
        audit.append(f"+{POINTS_PRICE_ABOVE_SMA200:.0f} pts: Price above 200-day SMA")
    if sma20 is not None and sma50 is not None and sma20 > sma50:
        points += POINTS_SMA20_ABOVE_SMA50
        audit.append(f"+{POINTS_SMA20_ABOVE_SMA50:.0f} pts: 20-day SMA above 50-day SMA (short-term uptrend)")

    if _seller_exhaustion(recent_closes, recent_volumes, avg_volume_20d):
        points += POINTS_SELLER_EXHAUSTION
        flags["seller_exhaustion"] = True
        audit.append(
            f"+{POINTS_SELLER_EXHAUSTION:.0f} pts: Seller exhaustion detected "
            f"(down-days in the last {SELLER_EXHAUSTION_LOOKBACK_DAYS}d all on below-average volume)"
        )
    return points


def _sector_flow_points(sector_flow_status: str | None, audit: list[str]) -> float:
    if sector_flow_status == "Accumulation":
        audit.append(f"+{POINTS_SECTOR_ACCUMULATION:.0f} pts: Sector in Accumulation")
        return POINTS_SECTOR_ACCUMULATION
    if sector_flow_status == "Distribution":
        audit.append(f"+{POINTS_SECTOR_DISTRIBUTION:.0f} pts: Sector in Distribution")
        return POINTS_SECTOR_DISTRIBUTION
    if sector_flow_status == "Neutral":
        audit.append(f"+{POINTS_SECTOR_NEUTRAL:.0f} pts: Sector flow Neutral")
        return POINTS_SECTOR_NEUTRAL
    audit.append(f"+{POINTS_SECTOR_NEUTRAL:.0f} pts: No sector flow data available (defaulted to neutral)")
    return POINTS_SECTOR_NEUTRAL


def _short_interest_points(
    short_percent_of_float: float | None, technical_points: float, audit: list[str], flags: dict
) -> float:
    if short_percent_of_float is None or short_percent_of_float <= SHORT_INTEREST_HIGH_THRESHOLD:
        return 0.0

    pct = short_percent_of_float * 100
    if technical_points >= TECHNICAL_HIGH_THRESHOLD:
        flags["short_squeeze_setup"] = True
        audit.append(
            f"+{POINTS_SHORT_SQUEEZE_BONUS:.0f} pts: Short Squeeze Setup - "
            f"{pct:.1f}% short interest combined with strong technicals"
        )
        return POINTS_SHORT_SQUEEZE_BONUS

    flags["bearish_conviction"] = True
    audit.append(
        f"{POINTS_BEARISH_CONVICTION_PENALTY:.0f} pts: Bearish Conviction - "
        f"{pct:.1f}% short interest confirms weak technicals"
    )
    return POINTS_BEARISH_CONVICTION_PENALTY


def _risk_modifier_points(
    atr_pct: float | None, has_recent_executive_sale: bool, audit: list[str], flags: dict
) -> float:
    points = 0.0
    if atr_pct is not None and atr_pct > ATR_VOLATILITY_PENALTY_THRESHOLD_PCT:
        points += POINTS_HIGH_VOLATILITY_PENALTY
        flags["high_volatility"] = True
        audit.append(
            f"{POINTS_HIGH_VOLATILITY_PENALTY:.0f} pts: High volatility - "
            f"ATR {atr_pct:.2f}% of price exceeds the {ATR_VOLATILITY_PENALTY_THRESHOLD_PCT:.1f}% threshold"
        )
    if has_recent_executive_sale:
        points += POINTS_INSIDER_SELLING_PENALTY
        flags["insider_selling"] = True
        audit.append(
            f"{POINTS_INSIDER_SELLING_PENALTY:.0f} pts: CEO/CFO insider selling "
            f"in the last {INSIDER_SELLING_LOOKBACK_DAYS} days"
        )
    return points


def compute_investment_score(
    *,
    closes: list[float],
    volumes: list[int],
    sma20_series: list[float | None],
    sma50_series: list[float | None],
    sma200_series: list[float | None],
    recent_sentiment: list[float | None],
    atr14: float | None,
    avg_volume_20d: float | None,
    sector_flow_status: str | None,
    short_percent_of_float: float | None,
    has_recent_executive_sale: bool,
) -> ScoreBreakdown:
    """`closes`/`volumes`/`sma*_series` must be ordered oldest-to-newest and
    the same length. `recent_sentiment` should be just the trailing
    SENTIMENT_LOOKBACK_DAYS window.
    """
    if len(closes) < MIN_PRICES_FOR_SCORE:
        return ScoreBreakdown(
            score=None,
            sentiment_points=None,
            technical_points=None,
            positioning_points=None,
            risk_modifier_points=None,
            reason=(
                f"needs {MIN_PRICES_FOR_SCORE} trading days of price history, has {len(closes)}"
            ),
        )

    audit: list[str] = []
    flags: dict = {}

    price = closes[-1]
    sma20 = _last_non_null(sma20_series)
    sma50 = _last_non_null(sma50_series)
    sma200 = _last_non_null(sma200_series)

    window = SELLER_EXHAUSTION_LOOKBACK_DAYS + 1
    recent_closes = closes[-window:]
    recent_volumes = volumes[-window:]

    weighted_sentiment = _weighted_recent_sentiment(recent_sentiment)
    atr_pct = (atr14 / price * 100) if (atr14 is not None and price) else None

    sentiment_points = _sentiment_points(weighted_sentiment, audit)
    technical_points = _technical_points(
        price, sma20, sma50, sma200, recent_closes, recent_volumes, avg_volume_20d, audit, flags
    )
    sector_points = _sector_flow_points(sector_flow_status, audit)
    short_interest_points = _short_interest_points(short_percent_of_float, technical_points, audit, flags)
    positioning_points = sector_points + short_interest_points
    risk_modifier_points = _risk_modifier_points(atr_pct, has_recent_executive_sale, audit, flags)

    raw_total = sentiment_points + technical_points + positioning_points + risk_modifier_points
    final_score = round(_clamp(raw_total, 0, 100))

    return ScoreBreakdown(
        score=final_score,
        sentiment_points=round(sentiment_points, 1),
        technical_points=round(technical_points, 1),
        positioning_points=round(positioning_points, 1),
        risk_modifier_points=round(risk_modifier_points, 1),
        audit_trail=audit,
        flags=flags,
    )
