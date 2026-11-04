"""Incremental technicals must write the SAME numbers a full run would, for every bar it touches."""
import random

import pytest

from marketpulse.technicals import (
    INCREMENTAL_LOOKBACK_BARS,
    INCREMENTAL_OVERLAP_BARS,
    DailyBar,
    compute_technicals,
)


def _bars(n, seed=7):
    rng = random.Random(seed)
    price, out = 100.0, []
    for _ in range(n):
        o = price * (1 + rng.uniform(-0.01, 0.01))
        c = o * (1 + rng.uniform(-0.03, 0.03))
        out.append(DailyBar(open=o, high=max(o, c) * 1.01, low=min(o, c) * 0.99, close=c, volume=rng.randint(1_000, 50_000)))
        price = c
    return out


FIELDS = ("sma20", "sma50", "sma150", "sma200", "atr14", "atr_90d_avg", "atr_90d_std", "avg_volume_20d", "gap_pct")


def test_windowed_recompute_matches_full_history_for_every_written_bar():
    bars = _bars(2500)  # ~10 years
    full = compute_technicals(bars)

    new_bars = 3
    write_n = new_bars + INCREMENTAL_OVERLAP_BARS
    window = bars[-(write_n + INCREMENTAL_LOOKBACK_BARS):]
    windowed = compute_technicals(window)[-write_n:]

    for got, want in zip(windowed, full[-write_n:]):
        for f in FIELDS:
            assert getattr(got, f) == pytest.approx(getattr(want, f), rel=1e-9), f
        # RSI is recursive: 250 bars of context leaves ~1e-8 of difference, shown at 1 decimal
        assert got.rsi14 == pytest.approx(want.rsi14, abs=1e-4)


def test_the_lookback_covers_every_indicator_that_needs_history():
    from marketpulse.technicals import ATR_PERIOD, ATR_ZSCORE_WINDOW

    assert INCREMENTAL_LOOKBACK_BARS >= 200  # SMA-200
    assert INCREMENTAL_LOOKBACK_BARS >= ATR_PERIOD + ATR_ZSCORE_WINDOW  # ATR-90 z-score


def test_moving_average_computation_is_linear_not_quadratic():
    import time

    small, large = _bars(500), _bars(4000)
    t0 = time.perf_counter(); compute_technicals(small); t_small = time.perf_counter() - t0
    t0 = time.perf_counter(); compute_technicals(large); t_large = time.perf_counter() - t0
    assert t_large < t_small * 8 * 3  # 8x the bars; a quadratic version would be ~64x
