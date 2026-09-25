from marketpulse.scoring import (
    MIN_PRICES_FOR_SCORE,
    SMA_LONG_PERIOD,
    compute_investment_score,
)


def _flat_prices(n: int, price: float = 100.0) -> list[float]:
    return [price] * n


def test_insufficient_history_returns_none_score_with_reason():
    closes = _flat_prices(SMA_LONG_PERIOD - 1)
    breakdown = compute_investment_score(closes, recent_sentiment=[])
    assert breakdown.score is None
    assert str(MIN_PRICES_FOR_SCORE) in breakdown.reason


def test_flat_prices_with_no_sentiment_yields_neutral_score():
    closes = _flat_prices(SMA_LONG_PERIOD + 10)
    breakdown = compute_investment_score(closes, recent_sentiment=[None] * 14)
    assert breakdown.score is not None
    assert breakdown.sentiment_score is None
    # Flat, unchanging prices: RSI is undefined-gain territory (avg_loss == 0) -> 100,
    # and price sits exactly on both SMAs -> trend baseline of 50, nudged by the
    # short/long SMA tie-break. Score should be squarely in the upper-middle range,
    # not pinned to either extreme.
    assert 40 <= breakdown.score <= 100


def test_uptrend_scores_higher_than_downtrend():
    n = SMA_LONG_PERIOD + 20
    uptrend = [100.0 + i * 0.5 for i in range(n)]
    downtrend = [100.0 - i * 0.5 for i in range(n)]

    up = compute_investment_score(uptrend, recent_sentiment=[])
    down = compute_investment_score(downtrend, recent_sentiment=[])

    assert up.score > down.score
    assert up.trend_score > down.trend_score
    assert up.momentum_score > down.momentum_score


def test_positive_sentiment_raises_score_over_no_sentiment():
    closes = _flat_prices(SMA_LONG_PERIOD + 10)
    neutral = compute_investment_score(closes, recent_sentiment=[])
    positive = compute_investment_score(closes, recent_sentiment=[0.9] * 14)

    assert positive.sentiment_score > 90
    assert positive.score > neutral.score


def test_score_is_clamped_between_0_and_100():
    n = SMA_LONG_PERIOD + 5
    extreme_up = [100.0 * (1.05**i) for i in range(n)]
    breakdown = compute_investment_score(extreme_up, recent_sentiment=[1.0] * 14)
    assert breakdown.score <= 100
