from marketpulse.scoring import (
    ATR_VOLATILITY_PENALTY_THRESHOLD_PCT,
    MIN_PRICES_FOR_SCORE,
    POINTS_BEARISH_CONVICTION_PENALTY,
    POINTS_HIGH_VOLATILITY_PENALTY,
    POINTS_INSIDER_SELLING_PENALTY,
    POINTS_SHORT_SQUEEZE_BONUS,
    SELLER_EXHAUSTION_LOOKBACK_DAYS,
    compute_investment_score,
)


def _base_kwargs(n=MIN_PRICES_FOR_SCORE + 10, price=100.0):
    closes = [price] * n
    volumes = [1000] * n
    smas = [price] * n
    return dict(
        closes=closes,
        volumes=volumes,
        sma20_series=list(smas),
        sma50_series=list(smas),
        sma200_series=list(smas),
        recent_sentiment=[],
        atr14=None,
        avg_volume_20d=None,
        sector_flow_status=None,
        short_percent_of_float=None,
        has_recent_executive_sale=False,
    )


def test_insufficient_history_returns_none_with_reason():
    kwargs = _base_kwargs(n=MIN_PRICES_FOR_SCORE - 1)
    result = compute_investment_score(**kwargs)
    assert result.score is None
    assert str(MIN_PRICES_FOR_SCORE) in result.reason


def test_flat_price_no_data_defaults_to_neutral_sector_only():
    # Flat price == flat SMAs (not strictly above), no sentiment/sector/risk data:
    # only the "no sector data, defaulted to neutral" rule fires (+10).
    kwargs = _base_kwargs()
    result = compute_investment_score(**kwargs)
    assert result.score == 10
    assert result.technical_points == 0.0
    assert result.sentiment_points == 0.0
    assert result.positioning_points == 10.0
    assert result.risk_modifier_points == 0.0
    assert any("neutral" in line.lower() for line in result.audit_trail)


def test_price_above_smas_awards_technical_points_and_logs_audit():
    kwargs = _base_kwargs()
    kwargs["closes"][-1] = 120.0  # price now above the (flat, 100) SMAs
    result = compute_investment_score(**kwargs)
    assert result.technical_points == 25.0  # +10 (>SMA50) +15 (>SMA200)
    assert any("50-day SMA" in line for line in result.audit_trail)
    assert any("200-day SMA" in line for line in result.audit_trail)


def test_sma20_above_sma50_adds_points():
    kwargs = _base_kwargs()
    kwargs["sma20_series"][-1] = 105.0
    kwargs["sma50_series"][-1] = 100.0
    kwargs["closes"][-1] = 90.0  # keep price below both SMA50/200 to isolate this rule
    result = compute_investment_score(**kwargs)
    assert result.technical_points == 10.0
    assert any("20-day SMA above 50-day SMA" in line for line in result.audit_trail)


def test_seller_exhaustion_bonus_when_all_down_days_are_low_volume():
    kwargs = _base_kwargs()
    window = SELLER_EXHAUSTION_LOOKBACK_DAYS + 1
    kwargs["closes"][-window:] = [110.0, 108.0, 106.0, 104.0, 102.0, 100.0][:window]
    kwargs["volumes"][-window:] = [500] * window
    kwargs["avg_volume_20d"] = 1000.0
    result = compute_investment_score(**kwargs)
    assert result.flags.get("seller_exhaustion") is True
    assert any("exhaustion" in line.lower() for line in result.audit_trail)


def test_seller_exhaustion_not_triggered_if_any_down_day_has_high_volume():
    kwargs = _base_kwargs()
    window = SELLER_EXHAUSTION_LOOKBACK_DAYS + 1
    kwargs["closes"][-window:] = [110.0, 108.0, 106.0, 104.0, 102.0, 100.0][:window]
    kwargs["volumes"][-window:] = [500, 500, 2000, 500, 500, 500][:window]
    kwargs["avg_volume_20d"] = 1000.0
    result = compute_investment_score(**kwargs)
    assert "seller_exhaustion" not in result.flags


def test_sentiment_points_scale_linearly_with_weighted_sentiment():
    kwargs = _base_kwargs()
    kwargs["recent_sentiment"] = [1.0, 1.0]  # maximally positive
    result = compute_investment_score(**kwargs)
    assert result.sentiment_points == 30.0

    kwargs["recent_sentiment"] = [-1.0, -1.0]  # maximally negative
    result = compute_investment_score(**kwargs)
    assert result.sentiment_points == 0.0


def test_short_squeeze_setup_when_technicals_are_strong():
    kwargs = _base_kwargs()
    kwargs["closes"][-1] = 130.0  # strong technicals: >SMA50, >SMA200
    kwargs["short_percent_of_float"] = 0.15
    result = compute_investment_score(**kwargs)
    assert result.flags.get("short_squeeze_setup") is True
    assert POINTS_SHORT_SQUEEZE_BONUS in [
        float(line.split()[0]) for line in result.audit_trail if "Short Squeeze" in line
    ]


def test_bearish_conviction_when_technicals_are_weak():
    kwargs = _base_kwargs()  # flat price -> 0 technical points -> "weak"
    kwargs["short_percent_of_float"] = 0.15
    result = compute_investment_score(**kwargs)
    assert result.flags.get("bearish_conviction") is True
    assert any(str(int(POINTS_BEARISH_CONVICTION_PENALTY)) in line for line in result.audit_trail)


def test_low_short_interest_triggers_neither_rule():
    kwargs = _base_kwargs()
    kwargs["short_percent_of_float"] = 0.02
    result = compute_investment_score(**kwargs)
    assert "short_squeeze_setup" not in result.flags
    assert "bearish_conviction" not in result.flags


def test_high_atr_percent_applies_volatility_penalty():
    kwargs = _base_kwargs(price=100.0)
    kwargs["atr14"] = 5.0  # 5% of price, above the 4.5% threshold
    result = compute_investment_score(**kwargs)
    assert result.flags.get("high_volatility") is True
    assert result.risk_modifier_points == POINTS_HIGH_VOLATILITY_PENALTY
    assert any(f"{ATR_VOLATILITY_PENALTY_THRESHOLD_PCT}" in line for line in result.audit_trail)


def test_low_atr_percent_no_penalty():
    kwargs = _base_kwargs(price=100.0)
    kwargs["atr14"] = 2.0  # 2% of price, below threshold
    result = compute_investment_score(**kwargs)
    assert "high_volatility" not in result.flags


def test_insider_selling_penalty_applied():
    kwargs = _base_kwargs()
    kwargs["has_recent_executive_sale"] = True
    result = compute_investment_score(**kwargs)
    assert result.flags.get("insider_selling") is True
    assert result.risk_modifier_points == POINTS_INSIDER_SELLING_PENALTY


def test_score_is_clamped_to_0_when_penalties_exceed_gains():
    kwargs = _base_kwargs()
    kwargs["atr14"] = 10.0  # big volatility penalty
    kwargs["has_recent_executive_sale"] = True  # insider penalty
    kwargs["short_percent_of_float"] = 0.20  # bearish conviction penalty (weak technicals)
    result = compute_investment_score(**kwargs)
    assert result.score == 0


def test_maximally_bullish_scenario_hits_100():
    kwargs = _base_kwargs()
    kwargs["closes"][-1] = 150.0  # price above both SMA50/200
    kwargs["sma20_series"][-1] = 149.0  # 20 SMA above 50 SMA
    window = SELLER_EXHAUSTION_LOOKBACK_DAYS + 1
    kwargs["closes"][-window:-1] = [140.0, 138.0, 136.0, 134.0, 132.0][: window - 1]
    kwargs["volumes"][-window:] = [500] * window
    kwargs["avg_volume_20d"] = 1000.0
    kwargs["recent_sentiment"] = [1.0]
    kwargs["sector_flow_status"] = "Accumulation"
    kwargs["short_percent_of_float"] = 0.15  # squeeze bonus, since technicals are strong
    result = compute_investment_score(**kwargs)
    assert result.score == 100
