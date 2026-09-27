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

DYNAMIC NORMALIZATION: a QA backtest found that stocks with no recent news
(the overwhelming majority of the watchlist, given how thin the RSS/FinBERT
pipeline's coverage is) can never exceed Technical(40)+Positioning(30)=70,
so "Strong Buy" (>70) was structurally unreachable for most of the universe
and, when it did fire, was almost entirely a sentiment artifact rather than
a technical/fundamental one. Rather than just lowering the UI's band
thresholds (which papers over the real issue - a missing 30-point bucket),
when there is NO scored sentiment in the lookback window at all, the
Technical+Positioning base is normalized from its 70-point ceiling up to a
100-point scale: normalized = (technical + positioning) / MAX_NO_SENTIMENT_BASE * 100.
Risk modifiers are applied AFTER normalization, not inside it, so a -20
insider-selling penalty always means -20 regardless of whether sentiment
data exists. See compute_investment_score() and MAX_NO_SENTIMENT_BASE.

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
MAX_TECHNICAL_POINTS = (
    POINTS_PRICE_ABOVE_SMA50 + POINTS_PRICE_ABOVE_SMA200 + POINTS_SMA20_ABOVE_SMA50 + POINTS_SELLER_EXHAUSTION
)

# --- Positioning & Macro (-10 to 30 pts) ---
POINTS_SECTOR_ACCUMULATION = 20.0
POINTS_SECTOR_NEUTRAL = 10.0
POINTS_SECTOR_DISTRIBUTION = 0.0
SHORT_INTEREST_HIGH_THRESHOLD = 0.10  # 10% of float
POINTS_SHORT_SQUEEZE_BONUS = 10.0
POINTS_BEARISH_CONVICTION_PENALTY = -10.0
# "HIGH" technical score for the short-interest rule: >= half of the 40-pt max
TECHNICAL_HIGH_THRESHOLD = 20.0
MAX_POSITIONING_POINTS = POINTS_SECTOR_ACCUMULATION + POINTS_SHORT_SQUEEZE_BONUS

# When there's no sentiment data at all, Technical+Positioning is normalized
# from this ceiling up to 100 - see the module docstring.
MAX_NO_SENTIMENT_BASE = MAX_TECHNICAL_POINTS + MAX_POSITIONING_POINTS

# --- Risk Modifiers (-30 to 0 pts) ---
# Relative, not absolute: a flat ATR% threshold unfairly penalizes stocks
# that are naturally volatile by nature (e.g. small-cap growth names) even
# when they're trading calmly *for them*. Instead we compare today's ATR-14
# against the stock's OWN trailing 90-day ATR distribution (see
# technicals.py's ATR_ZSCORE_WINDOW) and only penalize when it's a genuine
# outlier relative to its own history.
ATR_ZSCORE_PENALTY_THRESHOLD = 1.5  # standard deviations above the stock's own 90-day ATR average
POINTS_HIGH_VOLATILITY_PENALTY = -10.0  # flat penalty when triggered - never scales past this cap
POINTS_INSIDER_SELLING_PENALTY = -20.0
INSIDER_SELLING_LOOKBACK_DAYS = 90  # how far back a CEO/CFO sale still counts as "recent"

# --- "Sell the News" risk flag (informational only - does NOT affect the score) ---
SELL_THE_NEWS_SENTIMENT_THRESHOLD = 25.0  # sentiment_points out of MAX_SENTIMENT_POINTS
SELL_THE_NEWS_PRICE_EXTENSION_PCT = 10.0  # price this much %+ above SMA-20 counts as "overextended"
SELL_THE_NEWS_VOLUME_CLIMAX_MULTIPLIER = 2.0  # today's volume vs. the 20-day average counts as "climaxing"


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
    atr14: float | None,
    atr_90d_avg: float | None,
    atr_90d_std: float | None,
    has_recent_executive_sale: bool,
    audit: list[str],
    flags: dict,
) -> float:
    points = 0.0
    if atr14 is not None and atr_90d_avg is not None and atr_90d_std is not None and atr_90d_std > 0:
        z_score = (atr14 - atr_90d_avg) / atr_90d_std
        if z_score > ATR_ZSCORE_PENALTY_THRESHOLD:
            points += POINTS_HIGH_VOLATILITY_PENALTY
            flags["high_volatility"] = True
            audit.append(
                f"{POINTS_HIGH_VOLATILITY_PENALTY:.0f} pts: High volatility - "
                f"ATR is {z_score:.1f} SD above historical average"
            )
    if has_recent_executive_sale:
        points += POINTS_INSIDER_SELLING_PENALTY
        flags["insider_selling"] = True
        audit.append(
            f"{POINTS_INSIDER_SELLING_PENALTY:.0f} pts: CEO/CFO insider selling "
            f"in the last {INSIDER_SELLING_LOOKBACK_DAYS} days"
        )
    return points


def _check_sell_the_news(
    sentiment_points: float,
    price: float,
    sma20: float | None,
    avg_volume_20d: float | None,
    latest_volume: int | None,
    audit: list[str],
    flags: dict,
) -> None:
    """'Buy the rumor, sell the news': a very high sentiment score combined
    with already-overextended technicals (price far above its 20-day SMA, or
    a volume climax) can mean the news-driven move is already exhausted.
    Purely informational - a warning badge, never a point adjustment. Doesn't
    run at all unless sentiment is genuinely strong, so it can't fire on the
    normalized no-sentiment path (sentiment_points is 0 there)."""
    if sentiment_points <= SELL_THE_NEWS_SENTIMENT_THRESHOLD:
        return

    price_extended = (
        sma20 is not None and sma20 > 0 and price > sma20 * (1 + SELL_THE_NEWS_PRICE_EXTENSION_PCT / 100)
    )
    volume_climaxing = (
        avg_volume_20d is not None
        and avg_volume_20d > 0
        and latest_volume is not None
        and latest_volume > avg_volume_20d * SELL_THE_NEWS_VOLUME_CLIMAX_MULTIPLIER
    )
    if not (price_extended or volume_climaxing):
        return

    reasons = []
    if price_extended:
        reasons.append(f"price {(price / sma20 - 1) * 100:.1f}% above its 20-day SMA")
    if volume_climaxing:
        reasons.append("volume climaxing vs. its 20-day average")

    flags["sell_the_news_risk"] = True
    audit.append(
        f"⚠ Sell the News Risk: sentiment is very high ({sentiment_points:.1f}/{MAX_SENTIMENT_POINTS:.0f}) "
        f"but {' and '.join(reasons)} - the news-driven move may already be exhausted"
    )


def compute_investment_score(
    *,
    closes: list[float],
    volumes: list[int],
    sma20_series: list[float | None],
    sma50_series: list[float | None],
    sma200_series: list[float | None],
    recent_sentiment: list[float | None],
    atr14: float | None,
    atr_90d_avg: float | None = None,
    atr_90d_std: float | None = None,
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

    sentiment_points = _sentiment_points(weighted_sentiment, audit)
    technical_points = _technical_points(
        price, sma20, sma50, sma200, recent_closes, recent_volumes, avg_volume_20d, audit, flags
    )
    sector_points = _sector_flow_points(sector_flow_status, audit)
    short_interest_points = _short_interest_points(short_percent_of_float, technical_points, audit, flags)
    positioning_points = sector_points + short_interest_points
    risk_modifier_points = _risk_modifier_points(
        atr14, atr_90d_avg, atr_90d_std, has_recent_executive_sale, audit, flags
    )

    latest_volume = volumes[-1] if volumes else None
    _check_sell_the_news(sentiment_points, price, sma20, avg_volume_20d, latest_volume, audit, flags)

    if weighted_sentiment is None:
        # No news at all: normalize the Technical+Positioning base up from its
        # 70-point ceiling to 100, then apply risk modifiers on top - see the
        # module docstring for why.
        base = technical_points + positioning_points
        normalized_base = base / MAX_NO_SENTIMENT_BASE * 100
        scale = 100 / MAX_NO_SENTIMENT_BASE
        technical_points = technical_points * scale
        positioning_points = positioning_points * scale
        audit.append(
            f"Score normalized: no recent news, so the {base:.1f}/{MAX_NO_SENTIMENT_BASE:.0f}-point "
            f"Technical + Positioning base is scaled to a /100 base ({normalized_base:.1f}) before risk modifiers"
        )
        raw_total = normalized_base + risk_modifier_points
    else:
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
