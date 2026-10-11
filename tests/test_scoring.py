"""Investment Score v2: pure quantitative, three pillars, graded rules."""
import pytest

from marketpulse.scoring import (
    ATR_ZSCORE_PENALTY_THRESHOLD,
    MIN_PRICES_FOR_SCORE,
    MOMENTUM_MAX,
    PILLAR_MAX,
    RISK_MAX,
    SECTOR_MAX,
    TREND_MAX,
    compute_investment_score,
    rsi_points,
    signal_label,
)

N = MIN_PRICES_FOR_SCORE + 10


def _kw(price=100.0, sma20=100.0, sma50=100.0, sma200=100.0, **over):
    kw = dict(
        closes=[price] * N,
        sma20_series=[sma20] * N,
        sma50_series=[sma50] * N,
        sma200_series=[sma200] * N,
        rsi14=50.0,
        rsi14_prev=50.0,
        atr14=1.0,
        atr_90d_avg=1.0,
        atr_90d_std=0.2,
        sector_flow_status="Neutral",
        sector_rel_20d_pct=0.0,
        short_percent_of_float=0.03,
        has_recent_executive_sale=False,
    )
    kw.update(over)
    return kw


def score(**kw):
    return compute_investment_score(**kw)


def test_pillar_budgets_sum_to_100():
    assert TREND_MAX == 45 and MOMENTUM_MAX == 30 and SECTOR_MAX == 15 and RISK_MAX == 10
    assert sum(PILLAR_MAX.values()) == 100


def test_insufficient_history_returns_none_with_reason():
    r = score(**{**_kw(), "closes": [100.0] * (MIN_PRICES_FOR_SCORE - 1)})
    assert r.score is None and str(MIN_PRICES_FOR_SCORE) in r.reason


def test_the_score_takes_no_sentiment_or_news_input():
    with pytest.raises(TypeError):
        score(**_kw(), recent_sentiment=[0.9])
    r = score(**_kw())
    assert not any("sentiment" in line.lower() or "news" in line.lower() for line in r.audit_trail)


def test_a_neutral_stock_lands_mid_scale_with_half_trend_points():
    r = score(**_kw())  # price exactly on every average, RSI 50, neutral sector, 3% shorts, normal volatility
    assert r.trend_points == TREND_MAX / 2
    assert 49 <= r.score <= 54
    assert signal_label(r.score) == "Neutral / Mixed"


def test_trend_is_graded_not_binary():
    flat = score(**_kw()).trend_points
    slightly_above = score(**_kw(price=101.0)).trend_points  # +1% on every average
    far_above = score(**_kw(price=130.0)).trend_points
    below = score(**_kw(price=95.0)).trend_points
    assert below < flat < slightly_above < far_above
    assert far_above == pytest.approx(37.5)  # three price-vs-average rules maxed (30) + level 50/200 alignment (7.5)
    full = score(**_kw(price=130.0, sma20=120.0, sma50=110.0, sma200=100.0)).trend_points
    assert full == TREND_MAX


def test_death_cross_alignment_scores_zero_on_that_rule():
    r = score(**_kw(sma50=94.0, sma200=100.0))
    item = [i for i in r.items if "alignment" in i.text][0]
    assert item.points == 0.0 and "death-cross" in item.text


def test_rsi_curve_rewards_healthy_momentum_and_penalises_overbought():
    assert rsi_points(60) == MOMENTUM_MAX  # healthy bullish
    assert rsi_points(50) < rsi_points(58)  # neutral < healthy
    assert rsi_points(75) < rsi_points(65) and rsi_points(85) < rsi_points(75)  # overbought keeps shrinking
    assert rsi_points(85) < 3
    healthy = score(**_kw(rsi14=60.0)).score
    stretched = score(**_kw(rsi14=82.0)).score
    assert healthy - stretched >= 25


def test_overbought_stock_cannot_reach_80_even_with_a_perfect_everything_else():
    r = score(
        **_kw(
            price=130.0, sma20=120.0, sma50=110.0, sma200=100.0,
            rsi14=85.0, sector_flow_status="Accumulation", sector_rel_20d_pct=5.0,
        )
    )
    assert r.score < 90  # trend 45 + sector 15 + risk 10 = 70 max + ~1 momentum
    assert r.flags.get("overbought_rsi") is True
    assert any("overbought" in line and "lost" in line for line in r.audit_trail)


def test_oversold_recovery_is_rewarded_only_when_rsi_is_rising():
    rising = score(**_kw(rsi14=35.0, rsi14_prev=28.0))
    falling = score(**_kw(rsi14=35.0, rsi14_prev=42.0))
    assert rising.momentum_points > falling.momentum_points
    assert rising.flags.get("oversold_recovery") is True
    assert "oversold_recovery" not in falling.flags
    assert rising.momentum_points <= 22.0  # capped: a bounce never outranks healthy momentum


def test_sector_pillar_flow_and_relative_performance():
    acc = score(**_kw(sector_flow_status="Accumulation", sector_rel_20d_pct=3.0)).sector_points
    dist = score(**_kw(sector_flow_status="Distribution", sector_rel_20d_pct=-3.0)).sector_points
    assert acc == SECTOR_MAX and dist == 0.0


def test_missing_data_is_neutral_and_says_so():
    r = score(**_kw(sector_flow_status=None, sector_rel_20d_pct=None, rsi14=None, short_percent_of_float=None, atr14=None))
    assert r.sector_points == SECTOR_MAX / 2
    assert r.momentum_points == MOMENTUM_MAX / 2
    assert sum("no data" in line or "not enough" in line for line in r.audit_trail) >= 4


def test_short_interest_squeeze_vs_bearish_conviction():
    strong = score(**_kw(price=130.0, sma20=120.0, sma50=110.0, sma200=100.0, short_percent_of_float=0.20))
    weak = score(**_kw(price=80.0, short_percent_of_float=0.20))
    assert strong.flags.get("short_squeeze_setup") is True
    assert weak.flags.get("bearish_conviction") is True
    assert strong.risk_points > weak.risk_points
    assert score(**_kw(short_percent_of_float=0.0)).risk_points > score(**_kw(short_percent_of_float=0.08)).risk_points


def test_volatility_and_insider_selling_reduce_the_risk_pillar():
    calm = score(**_kw()).risk_points
    jumpy = score(**_kw(atr14=1.0 + (ATR_ZSCORE_PENALTY_THRESHOLD + 1) * 0.2)).risk_points
    sold = score(**_kw(has_recent_executive_sale=True)).risk_points
    assert jumpy < calm and sold < calm
    assert score(**_kw(short_percent_of_float=0.0, atr14=0.7)).risk_points == RISK_MAX  # best case: no shorts, calm, no sales


def test_score_equals_sum_of_pillars_and_audit_trail_explains_every_point():
    r = score(**_kw(price=108.0, sma20=104.0, sma50=102.0, sma200=96.0, rsi14=62.0, sector_flow_status="Accumulation", sector_rel_20d_pct=1.0))
    pillars = r.trend_points + r.momentum_points + r.sector_points + r.risk_points
    assert abs(r.score - pillars) <= 1.0  # only the final rounding
    assert sum(i.points for i in r.items) == pytest.approx(pillars, abs=0.2)
    assert {i.pillar for i in r.items} == {"trend", "momentum", "sector", "risk"}
    assert all(i.text for i in r.items) and len(r.audit_trail) == len(r.items)


def test_signal_labels_are_diagnostic_not_predictive():
    assert signal_label(70) == "Strong Signal" and signal_label(95) == "Strong Signal"
    assert signal_label(69) == "Neutral / Mixed" and signal_label(45) == "Neutral / Mixed"
    assert signal_label(44) == "Weak Signal"
    assert signal_label(None) is None


def test_score_is_continuous_so_near_identical_stocks_do_not_tie():
    a = score(**_kw(price=101.0, sma20=100.0, sma50=100.0, sma200=99.0, rsi14=52.2))
    b = score(**_kw(price=101.4, sma20=100.0, sma50=100.0, sma200=99.0, rsi14=52.2))
    assert a.score != b.score and b.score > a.score
    assert a.score != round(a.score)  # not an integer
    assert signal_label(69.6) == "Neutral / Mixed"  # bands use the exact value
