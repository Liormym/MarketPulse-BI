import importlib.util
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("evaluate_score_ic", ROOT / "scripts" / "evaluate_score_ic.py")
ev = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ev)

N_STOCKS = 60


def _panel(n_days, signal_strength, seed=0, score_days=None):
    """Synthetic market: stock i has score i; forward returns correlate with the score
    when signal_strength > 0, otherwise are pure noise."""
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2026-01-05", periods=n_days)
    assets = list(range(N_STOCKS))
    score = np.linspace(10, 90, N_STOCKS)
    prices = np.ones((n_days, N_STOCKS)) * 100.0
    for t in range(1, n_days):
        drift = signal_strength * (score - 50) / 5000
        prices[t] = prices[t - 1] * (1 + drift + rng.normal(0, 0.01, N_STOCKS))
    closes = pd.DataFrame(prices, index=dates, columns=assets)
    sd = dates[: score_days or n_days]
    rows = [
        {"asset": a, "date": d, "score": score[a], "TrendPoints": score[a] * 0.45,
         "MomentumPoints": rng.uniform(0, 30), "SectorPoints": 7.5, "RiskPoints": 5.0}
        for d in sd for a in assets
    ]
    return pd.DataFrame(rows), closes


def test_insufficient_data_is_reported_not_computed():
    scores, closes = _panel(n_days=8, signal_strength=1.0)
    res = {r["horizon_days"]: r for r in ev.evaluate(scores, closes)}
    assert res[60]["status"] == "INSUFFICIENT DATA" and "more trading day" in res[60]["detail"]
    assert res[20]["status"] == "INSUFFICIENT DATA"
    assert res[5]["status"] in ("PRELIMINARY", "OK")  # 3 usable dates -> preliminary
    assert res[60].get("mean_ic") is None
    report = ev.format_report(list(res.values()), 8, "2026-01-05", "2026-01-14")
    assert "INSUFFICIENT DATA" in report


def test_few_dates_are_labelled_preliminary_without_a_t_stat():
    scores, closes = _panel(n_days=14, signal_strength=1.0)
    r5 = ev.evaluate(scores, closes)[0]
    assert r5["status"] == "PRELIMINARY"
    assert r5["ic_t_newey_west"] is None and "anecdote" in r5["detail"]


def test_real_signal_gives_positive_ic_and_noise_gives_none():
    # Score history on 40 dates + 5 days of forward data for the last of them.
    scores, closes = _panel(n_days=200, signal_strength=25.0, score_days=60, seed=1)
    strong = ev.evaluate(scores, closes)[0]
    assert strong["status"] == "OK" and strong["mean_ic"] > 0.15
    assert strong["top_minus_bottom_quintile_pct"] > 0
    assert strong["component_mean_ic"]["Trend & Moving Averages"] > 0.15  # trend points track the score here

    scores, closes = _panel(n_days=200, signal_strength=0.0, score_days=60, seed=2)
    noise = ev.evaluate(scores, closes)[0]
    assert abs(noise["mean_ic"]) < 0.08


def test_newey_west_is_more_conservative_than_naive_for_autocorrelated_series():
    rng = np.random.default_rng(3)
    e = rng.normal(0, 1, 300)
    x = 0.1 + np.convolve(e, np.ones(10) / 10, mode="valid")  # strongly autocorrelated
    naive = x.mean() / (x.std(ddof=1) / np.sqrt(len(x)))
    assert abs(ev.newey_west_t(x, 9)) < abs(naive)
    assert ev.newey_west_t(np.array([1.0, 2.0]), 3) is None


def test_market_calendar_ignores_weekend_bars_from_a_few_assets():
    dates = pd.bdate_range("2026-03-02", periods=5)
    closes = pd.DataFrame(100.0, index=dates, columns=range(10))
    weekend = pd.Timestamp("2026-03-07")
    closes.loc[weekend] = np.nan
    closes.loc[weekend, 0] = 100.0  # one 24/7 asset only
    closes = closes.sort_index()
    assert weekend not in ev.market_calendar(closes)
