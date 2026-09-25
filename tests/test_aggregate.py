from datetime import date

from marketpulse.aggregate import ScoredArticle, aggregate_daily_sentiment


def test_confidence_weighted_average_favors_confident_articles():
    # A low-confidence positive (0.1) and a high-confidence negative (-0.9):
    # a plain mean would be +0.4 (net positive), but confidence-weighting
    # should let the confident negative article dominate, pulling the day
    # net negative instead.
    articles = [
        ScoredArticle("AAPL", date(2026, 1, 1), "Positive", 0.1),
        ScoredArticle("AAPL", date(2026, 1, 1), "Negative", -0.9),
    ]
    result = aggregate_daily_sentiment(articles)[0]
    assert result.avg_sentiment_score < 0


def test_equal_confidence_matches_plain_mean():
    articles = [
        ScoredArticle("AAPL", date(2026, 1, 1), "Positive", 0.8),
        ScoredArticle("AAPL", date(2026, 1, 1), "Negative", -0.8),
    ]
    result = aggregate_daily_sentiment(articles)[0]
    assert abs(result.avg_sentiment_score) < 1e-9


def test_articles_with_no_score_are_excluded_from_average_but_counted():
    articles = [
        ScoredArticle("AAPL", date(2026, 1, 1), "Positive", 0.9),
        ScoredArticle("AAPL", date(2026, 1, 1), None, None),  # failed FinBERT scoring
    ]
    result = aggregate_daily_sentiment(articles)[0]
    assert result.article_count == 2
    assert result.avg_sentiment_score == 0.9


def test_all_unscored_defaults_to_zero():
    articles = [ScoredArticle("AAPL", date(2026, 1, 1), None, None)]
    result = aggregate_daily_sentiment(articles)[0]
    assert result.avg_sentiment_score == 0.0
