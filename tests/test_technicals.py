from marketpulse.technicals import (
    ATR_PERIOD,
    ATR_ZSCORE_WINDOW,
    SMA_PERIODS,
    DailyBar,
    compute_technicals,
    describe_gap,
)


def _flat_bars(n: int, price: float = 100.0, volume: int = 1000) -> list[DailyBar]:
    return [DailyBar(open=price, high=price + 1, low=price - 1, close=price, volume=volume) for _ in range(n)]


def test_smas_are_none_until_each_window_fills():
    n = max(SMA_PERIODS) + 5
    bars = _flat_bars(n)
    results = compute_technicals(bars)

    for period in SMA_PERIODS:
        attr = f"sma{period}"
        assert getattr(results[period - 2], attr) is None  # window not full yet
        assert getattr(results[period - 1], attr) == 100.0  # window just filled


def test_atr_is_zero_for_bars_with_no_true_range_movement():
    n = ATR_PERIOD + 5
    bars = [DailyBar(open=100.0, high=100.0, low=100.0, close=100.0, volume=1000) for _ in range(n)]
    results = compute_technicals(bars)
    assert results[-1].atr14 == 0.0


def test_atr_reflects_a_volatility_spike():
    n = ATR_PERIOD + 5
    bars = _flat_bars(n)
    bars[-1] = DailyBar(open=100.0, high=120.0, low=90.0, close=110.0, volume=1000)
    results = compute_technicals(bars)
    assert results[-1].atr14 > results[-2].atr14


def test_gap_pct_computed_from_open_vs_prior_close():
    bars = _flat_bars(3)
    bars[2] = DailyBar(open=110.0, high=112.0, low=109.0, close=111.0, volume=1000)
    results = compute_technicals(bars)
    assert results[0].gap_pct is None  # no prior day
    assert round(results[2].gap_pct, 2) == 10.0  # (110 - 100) / 100 * 100


def test_avg_volume_20d_none_until_20_prior_days_exist():
    bars = _flat_bars(21, volume=500)
    results = compute_technicals(bars)
    assert results[19].avg_volume_20d is None
    assert results[20].avg_volume_20d == 500


def test_atr_90d_stats_none_until_90_atr_readings_exist():
    n = ATR_PERIOD + ATR_ZSCORE_WINDOW - 1  # exactly 90 ATR readings by the last bar
    bars = _flat_bars(n)
    results = compute_technicals(bars)
    assert results[-2].atr_90d_avg is None  # only 89 ATR readings so far
    assert results[-2].atr_90d_std is None
    assert results[-1].atr_90d_avg == 2.0  # 90th reading just filled the window
    assert results[-1].atr_90d_std == 0.0  # constant ATR across the window


def test_atr_90d_stats_reflect_a_volatility_spike():
    n = ATR_PERIOD + ATR_ZSCORE_WINDOW - 1
    bars = _flat_bars(n)
    bars[-1] = DailyBar(open=100.0, high=120.0, low=90.0, close=110.0, volume=1000)
    results = compute_technicals(bars)
    assert results[-1].atr_90d_avg > 2.0  # spike pulls the rolling average up
    assert results[-1].atr_90d_std > 0.0  # no longer a constant series


def test_describe_gap_up_returns_direction_and_price_bounds():
    gap = describe_gap(open_price=253.10, prev_close=250.50)
    assert gap.direction == "Up"
    assert gap.prev_close == 250.50
    assert gap.open == 253.10
    assert round(gap.gap_pct, 2) == round((253.10 - 250.50) / 250.50 * 100, 2)


def test_describe_gap_down_returns_direction_and_price_bounds():
    gap = describe_gap(open_price=98.0, prev_close=100.0)
    assert gap.direction == "Down"
    assert gap.gap_pct == -2.0


def test_describe_gap_none_without_a_prior_close():
    assert describe_gap(open_price=100.0, prev_close=None) is None
    assert describe_gap(open_price=100.0, prev_close=0.0) is None


def test_describe_gap_none_when_price_did_not_gap():
    assert describe_gap(open_price=100.0, prev_close=100.0) is None
