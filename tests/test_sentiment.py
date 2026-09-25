from marketpulse.sentiment import finbert


def test_classify_maps_label_and_score(monkeypatch):
    fake_pipeline = lambda text: [{"label": "positive", "score": 0.87}]
    monkeypatch.setattr(finbert, "_get_pipeline", lambda: fake_pipeline)

    result = finbert.classify("Company beats earnings estimates")

    assert result.category == "Positive"
    assert result.score == 0.87


def test_classify_negates_score_for_negative_label(monkeypatch):
    # FinBERT's raw output is the confidence of the winning label - always
    # positive, even when that label is Negative. The stored score must be
    # signed (-1..+1 per spec), so a confidently-Negative headline should
    # come back with a negative score.
    fake_pipeline = lambda text: [{"label": "negative", "score": 0.93}]
    monkeypatch.setattr(finbert, "_get_pipeline", lambda: fake_pipeline)

    result = finbert.classify("Company misses earnings, guidance slashed")

    assert result.category == "Negative"
    assert result.score == -0.93


def test_classify_keeps_neutral_score_positive(monkeypatch):
    fake_pipeline = lambda text: [{"label": "neutral", "score": 0.61}]
    monkeypatch.setattr(finbert, "_get_pipeline", lambda: fake_pipeline)

    result = finbert.classify("Company to present at industry conference")

    assert result.category == "Neutral"
    assert result.score == 0.61


def test_classify_returns_none_on_failure(monkeypatch):
    def raising_pipeline(*args, **kwargs):
        raise RuntimeError("model exploded")

    monkeypatch.setattr(finbert, "_get_pipeline", lambda: raising_pipeline)

    assert finbert.classify("Some headline") is None
