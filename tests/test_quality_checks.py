from datetime import date, datetime, timezone

from marketpulse.aggregate import ScoredArticle
from marketpulse.extract.news import NewsRecord
from marketpulse.extract.prices import PriceRecord
from marketpulse.quality.checks import (
    check_date_out_of_range,
    check_duplicate_news,
    check_duplicate_price_records,
    check_invalid_empty_headlines,
    check_invalid_numeric_values,
    check_missing_sentiment_result,
    check_missing_ticker,
)


def price(ticker="AAPL", d=date(2026, 3, 5), close=100.0, volume=1000):
    return PriceRecord(ticker=ticker, trade_date=d, close=close, volume=volume)


def news(ticker="AAPL", article_id="a1", title="Apple beats estimates"):
    return NewsRecord(
        article_id=article_id,
        ticker=ticker,
        published_at=datetime(2026, 3, 5, tzinfo=timezone.utc),
        title=title,
        source_url="https://example.com/a1",
    )


def test_check_missing_ticker_drops_unknown_tickers():
    records = [price(ticker="AAPL"), price(ticker="UNKNOWN")]
    good, result = check_missing_ticker(records, valid_tickers={"AAPL"})

    assert [r.ticker for r in good] == ["AAPL"]
    assert result.passed is False
    assert result.failed_records == 1


def test_check_missing_ticker_all_valid_passes():
    records = [price(ticker="AAPL"), price(ticker="MSFT")]
    good, result = check_missing_ticker(records, valid_tickers={"AAPL", "MSFT"})

    assert len(good) == 2
    assert result.passed is True
    assert result.failed_records == 0


def test_check_invalid_numeric_values_drops_negative_price_and_volume():
    records = [price(close=100.0, volume=500), price(close=-5.0, volume=500), price(close=100.0, volume=-1)]
    good, result = check_invalid_numeric_values(records)

    assert len(good) == 1
    assert result.failed_records == 2


def test_check_invalid_empty_headlines_drops_blank_titles():
    records = [news(title="Real headline"), news(article_id="a2", title="   "), news(article_id="a3", title="")]
    good, result = check_invalid_empty_headlines(records)

    assert len(good) == 1
    assert result.failed_records == 2


def test_check_duplicate_news_dedupes_by_article_id():
    records = [news(article_id="a1"), news(article_id="a1"), news(article_id="a2")]
    good, result = check_duplicate_news(records)

    assert len(good) == 2
    assert result.failed_records == 1


def test_check_duplicate_price_records_dedupes_by_ticker_and_date():
    records = [price(), price(), price(ticker="MSFT")]
    good, result = check_duplicate_price_records(records)

    assert len(good) == 2
    assert result.failed_records == 1


def test_check_missing_sentiment_result_flags_but_does_not_drop():
    scored = [
        ScoredArticle(ticker="AAPL", trade_date=date(2026, 3, 5), category="Positive", score=0.9),
        ScoredArticle(ticker="AAPL", trade_date=date(2026, 3, 5), category=None, score=None),
    ]
    result = check_missing_sentiment_result(scored)

    assert result.passed is False
    assert result.failed_records == 1


def test_check_date_out_of_range_drops_dates_outside_dimdate():
    valid_keys = {20260305, 20260306}
    records = [price(d=date(2026, 3, 5)), price(d=date(2021, 3, 31))]

    good, result = check_date_out_of_range(records, valid_keys)

    assert [r.trade_date for r in good] == [date(2026, 3, 5)]
    assert result.passed is False
    assert result.failed_records == 1


def test_check_date_out_of_range_supports_datetime_attr_for_news():
    valid_keys = {20260305}
    stale = news(article_id="old", title="Stale republished article")
    stale.published_at = datetime(2021, 3, 31, tzinfo=timezone.utc)
    fresh = news(article_id="fresh", title="Fresh article")
    fresh.published_at = datetime(2026, 3, 5, tzinfo=timezone.utc)

    good, result = check_date_out_of_range([stale, fresh], valid_keys, date_attr="published_at")

    assert [r.article_id for r in good] == ["fresh"]
    assert result.failed_records == 1
