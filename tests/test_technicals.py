from marketpulse.technicals import ATR_PERIOD, SMA_PERIODS, DailyBar, compute_technicals


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
