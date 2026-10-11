"""Investment Score v2: a fully explainable, 100% quantitative 0-100 composite
of current technical and sector alignment. NO news, NO sentiment, NO insider
text mining: only prices, volume and positioning data.

It is a DIAGNOSTIC SNAPSHOT of how a stock looks right now, not a forecast.
(A point-in-time backtest of the previous version found no predictive power,
see README; v2 makes no claim to have fixed that, it only makes the picture
cleaner and every point traceable.)

Three pillars plus one explicit modifier, one scale for every stock:

    Pillar 1  Trend & moving averages          45 pts
        price vs SMA20 (10) + vs SMA50 (10) + vs SMA200 (10) + SMA50 vs SMA200 (15)
    Pillar 2  Momentum, RSI-14 regime          30 pts
        healthy bullish momentum scores highest; neutral RSI gets moderate
        points; oversold stocks that are recovering are rewarded; overbought
        (>70) is progressively penalised so stretched stocks cannot reach 80+
    Pillar 3  Sector & risk/short profile      25 pts
        sector flow (10) + sector 20-day performance vs the S&P 500 (5)
        short interest (6) + volatility vs own history (4)
        (a typical stock earns about 6 of these 10)
    Modifier  CEO/CFO insider activity, last 90 days   -5 / 0 / +5 pts
        net selling = exactly -5.0 and the red "Insider Selling" badge;
        net buying  = exactly +5.0 and the green "Insider Buying" badge;
        no activity = 0 and no badge. It is added to the pillar total and the
        result is clamped to 0-100. Points and badge come from ONE value
        (`insider_net`), so they cannot disagree.

GRADED, NOT BINARY. The v1 rules were all-or-nothing ("price above SMA50: +10,
else 0"), which scored every ordinary stock harshly. Here each moving-average
component is a smooth ramp: HALF the points when price sits exactly on the
average, full points a "band" above it, zero a band below. So a stock with no
edge either way lands near the middle of every pillar, and a neutral stock
scores about 50-55 overall. Missing data counts as neutral (half points) and
says so in the audit trail, it never silently counts as bad.

Every rule appends a line to the audit trail (pillar, points earned, points
possible, plain-English reason); the UI shows them all.

Needs 200 trading days of price (SMA-200) to compute at all. Returns
score=None with a reason otherwise, rather than a number computed on too
little data. All constants are documented calibration heuristics, deliberately
easy to retune, not a fitted model.
"""
from dataclasses import dataclass, field

SCORE_VERSION = 2
MIN_PRICES_FOR_SCORE = 200

# --- Pillar budgets (sum to exactly 100) ---
TREND_MAX = 45.0
MOMENTUM_MAX = 30.0
SECTOR_MAX = 15.0
RISK_MAX = 10.0
PILLAR_MAX = {"trend": TREND_MAX, "momentum": MOMENTUM_MAX, "sector": SECTOR_MAX, "risk": RISK_MAX}

# --- Pillar 1: (label, max points, band as a fraction of the average) ---
PTS_PRICE_VS_SMA20, BAND_SMA20 = 10.0, 0.03
PTS_PRICE_VS_SMA50, BAND_SMA50 = 10.0, 0.05
PTS_PRICE_VS_SMA200, BAND_SMA200 = 10.0, 0.10
PTS_SMA50_VS_SMA200, BAND_SMA_ALIGN = 15.0, 0.05

# --- Pillar 2: RSI -> points, piecewise linear between these nodes ---
RSI_POINT_NODES = [
    (0, 6.0), (20, 8.0), (30, 10.0),  # oversold: some credit, falling knives are risky
    (40, 10.0), (45, 12.0), (50, 15.0),  # neutral: moderate points
    (55, 24.0), (58, 30.0), (65, 30.0),  # healthy bullish momentum: full points
    (70, 20.0), (75, 8.0), (80, 2.0), (100, 0.0),  # overbought: progressively penalised
]
RSI_HEALTHY_LOW, RSI_HEALTHY_HIGH = 45.0, 65.0  # the "healthy bullish" regime named in the UI
RSI_OVERBOUGHT = 70.0
RSI_RECOVERY_BELOW = 45.0  # an RSI under this that is rising counts as a recovery setup
RSI_RECOVERY_MIN_RISE = 3.0  # RSI points gained over the comparison window
RSI_RECOVERY_BONUS = 8.0
RSI_RECOVERY_CAP = 22.0
RSI_COMPARE_BARS = 5

# --- Pillar 3 ---
PTS_SECTOR_FLOW = {"Accumulation": 10.0, "Neutral": 5.0, "Distribution": 0.0}
PTS_SECTOR_FLOW_MAX = 10.0
PTS_SECTOR_RELATIVE_MAX = 5.0
SECTOR_RELATIVE_BARS_DEFAULT = 20  # trading bars compared for sector vs S&P 500 (webapp/db.py uses the same)
SECTOR_RELATIVE_BAND_PCT = 3.0  # sector 20d return vs S&P 500: +3 pts-of-percent = full, -3 = zero
PTS_SHORT_MAX = 6.0
SHORT_HIGH = 0.10  # fraction of float: zero short-interest points at/above this (squeeze exception below)
PTS_SHORT_SQUEEZE = 4.8
SQUEEZE_MIN_TREND_FRACTION = 0.6  # trend pillar share needed to read heavy shorting as a squeeze setup
PTS_VOLATILITY_MAX = 4.0
ATR_ZSCORE_PENALTY_THRESHOLD = 1.5  # SD above the stock's own 90-day ATR average
INSIDER_MODIFIER_POINTS = 5.0  # +/- applied for CEO/CFO net buying / net selling
INSIDER_LOOKBACK_DAYS = 90  # window for "recent" CEO/CFO trades (see marketpulse.insider)


@dataclass
class ScoreItem:
    pillar: str  # trend | momentum | sector | risk
    points: float
    max_points: float
    text: str


@dataclass
class ScoreBreakdown:
    score: float | None  # continuous 0-100 (4 dp), NOT rounded to an integer
    trend_points: float | None = None
    momentum_points: float | None = None
    sector_points: float | None = None
    risk_points: float | None = None
    insider_points: float | None = None  # -5.0 / 0.0 / +5.0, added on top of the pillars
    items: list[ScoreItem] = field(default_factory=list)
    flags: dict = field(default_factory=dict)
    reason: str | None = None
    version: int = SCORE_VERSION

    @property
    def audit_trail(self) -> list[str]:
        return [f"{i.points:.1f}/{i.max_points:.0f} pts [{i.pillar}]: {i.text}" for i in self.items]


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _last_non_null(values: list[float | None]) -> float | None:
    for v in reversed(values):
        if v is not None:
            return v
    return None


def _ramp(distance: float, full_points: float, band: float) -> float:
    """Half the points at distance 0, full at +band, zero at -band."""
    return full_points * _clamp(0.5 + distance / (2 * band), 0.0, 1.0)


def _pct(x: float) -> str:
    return f"{x * 100:+.1f}%"


def rsi_points(rsi: float) -> float:
    for (x0, y0), (x1, y1) in zip(RSI_POINT_NODES, RSI_POINT_NODES[1:]):
        if x0 <= rsi <= x1:
            return y0 + (y1 - y0) * (rsi - x0) / (x1 - x0)
    return 0.0


# --------------------------------------------------------------------------
# Pillar 1: trend
# --------------------------------------------------------------------------
def _trend_items(price, sma20, sma50, sma200) -> list[ScoreItem]:
    items = []

    def vs_average(label, avg, full, band):
        if avg is None or avg <= 0:
            items.append(ScoreItem("trend", full / 2, full, f"{label}: no data, counted as neutral (half points)"))
            return
        d = price / avg - 1
        pts = _ramp(d, full, band)
        items.append(
            ScoreItem(
                "trend", pts, full,
                f"Price {_pct(d)} vs {label} (half points on the average, full at +{band * 100:.0f}%, zero at -{band * 100:.0f}%)",
            )
        )

    vs_average("20-day SMA", sma20, PTS_PRICE_VS_SMA20, BAND_SMA20)
    vs_average("50-day SMA", sma50, PTS_PRICE_VS_SMA50, BAND_SMA50)
    vs_average("200-day SMA", sma200, PTS_PRICE_VS_SMA200, BAND_SMA200)

    if sma50 is None or sma200 is None or sma200 <= 0:
        items.append(ScoreItem("trend", PTS_SMA50_VS_SMA200 / 2, PTS_SMA50_VS_SMA200, "50/200-day SMA alignment: no data, counted as neutral"))
    else:
        d = sma50 / sma200 - 1
        label = "golden-cross alignment" if d > 0 else "death-cross alignment"
        items.append(
            ScoreItem(
                "trend", _ramp(d, PTS_SMA50_VS_SMA200, BAND_SMA_ALIGN), PTS_SMA50_VS_SMA200,
                f"50-day SMA {_pct(d)} vs 200-day SMA ({label}; full at +{BAND_SMA_ALIGN * 100:.0f}%, zero at -{BAND_SMA_ALIGN * 100:.0f}%)",
            )
        )
    return items


# --------------------------------------------------------------------------
# Pillar 2: momentum / RSI regime
# --------------------------------------------------------------------------
def _momentum_items(rsi: float | None, rsi_prev: float | None, flags: dict) -> list[ScoreItem]:
    if rsi is None:
        return [ScoreItem("momentum", MOMENTUM_MAX / 2, MOMENTUM_MAX, "RSI-14: no data, counted as neutral (half points)")]

    pts = rsi_points(rsi)
    if RSI_HEALTHY_LOW <= rsi <= RSI_HEALTHY_HIGH:
        text = f"RSI-14 is {rsi:.1f}: healthy bullish momentum range (45-65)"
    elif rsi > RSI_OVERBOUGHT:
        flags["overbought_rsi"] = True
        text = (
            f"RSI-14 is {rsi:.1f}: overbought (above 70), so momentum is penalised "
            f"({MOMENTUM_MAX - pts:.1f} pts lost) because stretched stocks tend to mean-revert"
        )
    elif rsi > RSI_HEALTHY_HIGH:
        text = f"RSI-14 is {rsi:.1f}: strong but approaching overbought (65-70), slightly reduced"
    elif rsi >= 30:
        text = f"RSI-14 is {rsi:.1f}: soft to neutral momentum, moderate points"
    else:
        text = f"RSI-14 is {rsi:.1f}: oversold, some credit but no confirmation of a bounce"

    if rsi < RSI_RECOVERY_BELOW and rsi_prev is not None and rsi - rsi_prev >= RSI_RECOVERY_MIN_RISE:
        boosted = min(RSI_RECOVERY_CAP, pts + RSI_RECOVERY_BONUS)
        if boosted > pts:
            flags["oversold_recovery"] = True
            text = (
                f"RSI-14 is {rsi:.1f} and rising ({rsi - rsi_prev:+.1f} over {RSI_COMPARE_BARS} bars): "
                f"recovery setup, +{boosted - pts:.1f} pts"
            )
            pts = boosted
    return [ScoreItem("momentum", pts, MOMENTUM_MAX, text)]


# --------------------------------------------------------------------------
# Pillar 3: sector + risk/short
# --------------------------------------------------------------------------
def _sector_items(flow_status: str | None, sector_rel_20d_pct: float | None) -> list[ScoreItem]:
    items = []
    if flow_status in PTS_SECTOR_FLOW:
        items.append(ScoreItem("sector", PTS_SECTOR_FLOW[flow_status], PTS_SECTOR_FLOW_MAX, f"Sector money-flow: {flow_status}"))
    else:
        items.append(ScoreItem("sector", PTS_SECTOR_FLOW_MAX / 2, PTS_SECTOR_FLOW_MAX, "Sector money-flow: no data, counted as neutral"))

    if sector_rel_20d_pct is None:
        items.append(ScoreItem("sector", PTS_SECTOR_RELATIVE_MAX / 2, PTS_SECTOR_RELATIVE_MAX, "Sector vs S&P 500 (20 days): no data, counted as neutral"))
    else:
        pts = _ramp(sector_rel_20d_pct, PTS_SECTOR_RELATIVE_MAX, SECTOR_RELATIVE_BAND_PCT)
        items.append(
            ScoreItem(
                "sector", pts, PTS_SECTOR_RELATIVE_MAX,
                f"Sector {sector_rel_20d_pct:+.1f} percentage points vs the S&P 500 over 20 days "
                f"(half points when level, full at +{SECTOR_RELATIVE_BAND_PCT:.0f}, zero at -{SECTOR_RELATIVE_BAND_PCT:.0f} points)",
            )
        )
    return items


def _risk_items(trend_points, short_pct, atr14, atr_avg, atr_std, flags) -> list[ScoreItem]:
    """Graded like the trend rules: a typical stock earns about half, a clearly
    low-risk one approaches the maximum, a clearly risky one approaches zero."""
    items = []

    if short_pct is None:
        items.append(ScoreItem("risk", PTS_SHORT_MAX / 2, PTS_SHORT_MAX, "Short interest: no data, counted as neutral"))
    elif short_pct > SHORT_HIGH and trend_points >= SQUEEZE_MIN_TREND_FRACTION * TREND_MAX:
        flags["short_squeeze_setup"] = True
        items.append(
            ScoreItem("risk", PTS_SHORT_SQUEEZE, PTS_SHORT_MAX, f"Short interest {short_pct * 100:.1f}% of float with a strong trend: squeeze setup")
        )
    elif short_pct > SHORT_HIGH:
        flags["bearish_conviction"] = True
        items.append(
            ScoreItem("risk", 0.0, PTS_SHORT_MAX, f"Short interest {short_pct * 100:.1f}% of float with a weak trend: bearish conviction")
        )
    else:
        pts = PTS_SHORT_MAX * (1 - short_pct / SHORT_HIGH)
        items.append(
            ScoreItem("risk", pts, PTS_SHORT_MAX, f"Short interest {short_pct * 100:.1f}% of float (full points at 0%, zero at {SHORT_HIGH * 100:.0f}%)")
        )

    if atr14 is None or atr_avg is None or atr_std is None or atr_std <= 0:
        items.append(ScoreItem("risk", PTS_VOLATILITY_MAX / 2, PTS_VOLATILITY_MAX, "Volatility: not enough ATR history, counted as neutral"))
    else:
        z = (atr14 - atr_avg) / atr_std
        if z > ATR_ZSCORE_PENALTY_THRESHOLD:
            flags["high_volatility"] = True
        pts = _ramp(-z, PTS_VOLATILITY_MAX, ATR_ZSCORE_PENALTY_THRESHOLD)
        items.append(
            ScoreItem(
                "risk", pts, PTS_VOLATILITY_MAX,
                f"Volatility: ATR is {z:+.1f} SD vs its own 90-day average "
                f"(half points at its norm, zero at +{ATR_ZSCORE_PENALTY_THRESHOLD:.1f} SD)",
            )
        )

    return items


def _insider_item(insider_net: str | None, flags: dict) -> ScoreItem:
    """The ONE place the insider badge and the insider points are decided."""
    if insider_net == "selling":
        flags["insider_selling"] = True
        return ScoreItem("insider", -INSIDER_MODIFIER_POINTS, INSIDER_MODIFIER_POINTS,
                         f"CEO/CFO net selling in the last {INSIDER_LOOKBACK_DAYS} days")
    if insider_net == "buying":
        flags["insider_buying"] = True
        return ScoreItem("insider", INSIDER_MODIFIER_POINTS, INSIDER_MODIFIER_POINTS,
                         f"CEO/CFO net buying in the last {INSIDER_LOOKBACK_DAYS} days")
    return ScoreItem("insider", 0.0, INSIDER_MODIFIER_POINTS,
                     f"No CEO/CFO net buying or selling in the last {INSIDER_LOOKBACK_DAYS} days")


def compute_investment_score(
    *,
    closes: list[float],
    sma20_series: list[float | None],
    sma50_series: list[float | None],
    sma200_series: list[float | None],
    rsi14: float | None,
    rsi14_prev: float | None = None,
    atr14: float | None = None,
    atr_90d_avg: float | None = None,
    atr_90d_std: float | None = None,
    sector_flow_status: str | None = None,
    sector_rel_20d_pct: float | None = None,
    short_percent_of_float: float | None = None,
    insider_net: str | None = None,
) -> ScoreBreakdown:
    """`closes` and the `sma*_series` are ordered oldest-to-newest; only the
    newest values are used. `rsi14_prev` is the RSI RSI_COMPARE_BARS bars ago."""
    if len(closes) < MIN_PRICES_FOR_SCORE:
        return ScoreBreakdown(
            score=None, reason=f"needs {MIN_PRICES_FOR_SCORE} trading days of price history, has {len(closes)}"
        )

    flags: dict = {}
    price = closes[-1]
    trend = _trend_items(price, _last_non_null(sma20_series), _last_non_null(sma50_series), _last_non_null(sma200_series))
    trend_pts = sum(i.points for i in trend)
    momentum = _momentum_items(rsi14, rsi14_prev, flags)
    sector = _sector_items(sector_flow_status, sector_rel_20d_pct)
    risk = _risk_items(trend_pts, short_percent_of_float, atr14, atr_90d_avg, atr_90d_std, flags)
    insider = _insider_item(insider_net, flags)

    pillars = {
        "trend": trend_pts,
        "momentum": sum(i.points for i in momentum),
        "sector": sum(i.points for i in sector),
        "risk": sum(i.points for i in risk),
    }
    return ScoreBreakdown(
        # Continuous on purpose: rounding to an integer created 5-way ties at the
        # top of the ranking; the UI shows one decimal and ranks on this value.
        score=round(_clamp(sum(pillars.values()) + insider.points, 0, 100), 4),
        trend_points=round(pillars["trend"], 1),
        momentum_points=round(pillars["momentum"], 1),
        sector_points=round(pillars["sector"], 1),
        risk_points=round(pillars["risk"], 1),
        insider_points=insider.points,
        items=trend + momentum + sector + risk + [insider],
        flags=flags,
    )


def signal_label(score: float | None) -> str | None:
    """Diagnostic band names (never predictive wording). Mirrored in the UI."""
    if score is None:
        return None
    if score >= 70:
        return "Strong Signal"
    if score >= 45:
        return "Neutral / Mixed"
    return "Weak Signal"
