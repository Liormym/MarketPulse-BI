import pytest

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


# ---------------------------------------------------------------- RSI
from marketpulse.technicals import (  # noqa: E402
    RSI_OVERBOUGHT,
    RSI_OVERSOLD,
    RSI_PERIOD,
    compute_rsi_series,
    describe_rsi,
)


def test_rsi_matches_a_hand_computed_wilder_example():
    # period=3, closes 10,11,12,11,13. Changes: +1,+1,-1,+2.
    # Seed (mean of first 3 changes): avg_gain=2/3, avg_loss=1/3 -> RS=2 -> RSI=66.667
    # Next (change +2), Wilder smoothing: avg_gain=(2/3*2+2)/3=10/9, avg_loss=(1/3*2+0)/3=2/9
    #   RS=5 -> RSI=83.333
    rsi = compute_rsi_series([10, 11, 12, 11, 13], period=3)
    assert rsi[:3] == [None, None, None]
    assert rsi[3] == pytest.approx(200 / 3)
    assert rsi[4] == pytest.approx(250 / 3)


def test_rsi_is_100_when_price_only_rises():
    rsi = compute_rsi_series([float(i) for i in range(1, 40)])
    assert rsi[RSI_PERIOD] == 100.0
    assert rsi[-1] == 100.0


def test_rsi_is_0_when_price_only_falls():
    rsi = compute_rsi_series([float(100 - i) for i in range(40)])
    assert rsi[-1] == pytest.approx(0.0)


def test_rsi_is_undefined_for_a_perfectly_flat_price():
    assert all(v is None for v in compute_rsi_series([50.0] * 40))


def test_rsi_needs_more_closes_than_the_period():
    assert compute_rsi_series([1.0] * RSI_PERIOD) == [None] * RSI_PERIOD
    assert compute_rsi_series([]) == []


def test_rsi_stays_within_bounds_on_noisy_data():
    import random

    rng = random.Random(7)
    closes = [100.0]
    for _ in range(500):
        closes.append(max(1.0, closes[-1] * (1 + rng.uniform(-0.05, 0.05))))
    values = [v for v in compute_rsi_series(closes) if v is not None]
    assert values and all(0.0 <= v <= 100.0 for v in values)


def test_rsi_has_no_lookahead():
    closes = [100 + (i % 7) * 1.5 - (i % 3) for i in range(80)]
    full = compute_rsi_series(closes)
    for cut in (30, 50, 79):
        assert compute_rsi_series(closes[: cut + 1])[cut] == pytest.approx(full[cut])


def test_compute_technicals_carries_the_rsi_series():
    bars = [DailyBar(open=c, high=c + 1, low=c - 1, close=c, volume=1000) for c in [100 + i * 0.5 for i in range(30)]]
    results = compute_technicals(bars)
    assert results[RSI_PERIOD - 1].rsi14 is None
    assert results[RSI_PERIOD].rsi14 == 100.0


def test_describe_rsi_zones_and_boundaries():
    assert describe_rsi(None) is None
    assert describe_rsi(RSI_OVERSOLD - 0.1) == "oversold"
    assert describe_rsi(RSI_OVERSOLD) == "neutral"
    assert describe_rsi(RSI_OVERBOUGHT) == "neutral"
    assert describe_rsi(RSI_OVERBOUGHT + 0.1) == "overbought"
    assert describe_rsi(50.0) == "neutral"
