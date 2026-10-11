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
        insider_net=None,
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


def test_volatility_reduces_the_risk_pillar_and_best_case_reaches_its_max():
    calm = score(**_kw()).risk_points
    jumpy = score(**_kw(atr14=1.0 + (ATR_ZSCORE_PENALTY_THRESHOLD + 1) * 0.2)).risk_points
    assert jumpy < calm
    assert score(**_kw(short_percent_of_float=0.0, atr14=0.7)).risk_points == RISK_MAX  # no shorts, very calm


def test_risk_pillar_is_six_points_short_interest_plus_four_volatility():
    best = score(**_kw(short_percent_of_float=0.0, atr14=0.7))
    risk_items = [i for i in best.items if i.pillar == "risk"]
    assert [i.max_points for i in risk_items] == [6.0, 4.0]
    assert sum(i.points for i in risk_items) == RISK_MAX == 10.0


def test_insider_net_selling_is_exactly_minus_five_with_a_red_badge():
    base = score(**_kw())
    sold = score(**_kw(insider_net="selling"))
    assert sold.insider_points == -5.0
    assert sold.score == pytest.approx(base.score - 5.0)
    assert sold.flags.get("insider_selling") is True and "insider_buying" not in sold.flags
    assert sold.risk_points == base.risk_points  # a modifier on top of the pillars, not buried in one
    line = [i for i in sold.items if i.pillar == "insider"][0]
    assert line.points == -5.0 and "CEO/CFO net selling in the last 90 days" in line.text


def test_insider_net_buying_is_exactly_plus_five_with_a_green_badge():
    base = score(**_kw())
    bought = score(**_kw(insider_net="buying"))
    assert bought.insider_points == 5.0
    assert bought.score == pytest.approx(base.score + 5.0)
    assert bought.flags.get("insider_buying") is True and "insider_selling" not in bought.flags
    assert "CEO/CFO net buying in the last 90 days" in [i for i in bought.items if i.pillar == "insider"][0].text


def test_no_insider_activity_is_zero_points_and_no_badge():
    r = score(**_kw(insider_net=None))
    assert r.insider_points == 0.0
    assert "insider_selling" not in r.flags and "insider_buying" not in r.flags
    assert [i for i in r.items if i.pillar == "insider"][0].points == 0.0


@pytest.mark.parametrize("net", [None, "selling", "buying"])
def test_badge_and_points_are_always_synchronised(net):
    r = score(**_kw(insider_net=net))
    assert (r.flags.get("insider_selling", False), r.flags.get("insider_buying", False)) == (
        net == "selling",
        net == "buying",
    )
    assert r.insider_points == {None: 0.0, "selling": -5.0, "buying": 5.0}[net]


def test_insider_modifier_is_clamped_to_the_0_to_100_range():
    top = score(**_kw(price=130.0, sma20=120.0, sma50=110.0, sma200=100.0, rsi14=60.0,
                      sector_flow_status="Accumulation", sector_rel_20d_pct=5.0,
                      short_percent_of_float=0.0, atr14=0.7, insider_net="buying"))
    assert top.score == 100.0  # 100 from the pillars + 5, clamped
    bottom = score(**_kw(price=70.0, sma20=100.0, sma50=110.0, sma200=120.0, rsi14=5.0,
                         sector_flow_status="Distribution", sector_rel_20d_pct=-9.0,
                         short_percent_of_float=0.2, atr14=3.0, insider_net="selling"))
    assert bottom.score >= 0.0


def test_score_equals_sum_of_pillars_and_audit_trail_explains_every_point():
    r = score(**_kw(price=108.0, sma20=104.0, sma50=102.0, sma200=96.0, rsi14=62.0, sector_flow_status="Accumulation", sector_rel_20d_pct=1.0))
    pillars = r.trend_points + r.momentum_points + r.sector_points + r.risk_points
    assert abs(r.score - (pillars + r.insider_points)) <= 0.5  # only the pillar rounding
    assert sum(i.points for i in r.items) == pytest.approx(r.score, abs=0.2)  # every point is in the trail
    assert {i.pillar for i in r.items} == {"trend", "momentum", "sector", "risk", "insider"}
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
