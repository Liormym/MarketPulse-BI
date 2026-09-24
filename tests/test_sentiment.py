from marketpulse.sentiment import finbert


def test_classify_maps_label_and_score(monkeypatch):
    fake_pipeline = lambda text: [{"label": "positive", "score": 0.87}]
    monkeypatch.setattr(finbert, "_get_pipeline", lambda: fake_pipeline)

    result = finbert.classify("Company beats earnings estimates")

    assert result.category == "Positive"
    assert result.score == 0.87


def test_classify_returns_none_on_failure(monkeypatch):
    def raising_pipeline(*args, **kwargs):
        raise RuntimeError("model exploded")

    monkeypatch.setattr(finbert, "_get_pipeline", lambda: raising_pipeline)

    assert finbert.classify("Some headline") is None
