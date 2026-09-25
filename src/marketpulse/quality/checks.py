"""Implements the 7 data-quality rules from spec §8.

Each check function takes the candidate records and known-good reference data
(e.g. the set of valid tickers), and returns (clean_records, CheckResult).
CheckResult rows are written verbatim to DataQualityResults by the pipeline.
"""
from dataclasses import dataclass


@dataclass
class CheckResult:
    check_type: str
    passed: bool
    failed_records: int
    details: str


def check_missing_ticker(records: list, valid_tickers: set[str], ticker_attr: str = "ticker"):
    good, bad = [], []
    for r in records:
        (good if getattr(r, ticker_attr) in valid_tickers else bad).append(r)
    result = CheckResult(
        check_type="MissingTicker",
        passed=len(bad) == 0,
        failed_records=len(bad),
        details=f"unknown tickers dropped: {sorted({getattr(r, ticker_attr) for r in bad})}" if bad else "",
    )
    return good, result


def check_missing_date(records: list, date_attr: str = "trade_date"):
    good, bad = [], []
    for r in records:
        (good if getattr(r, date_attr) is not None else bad).append(r)
    result = CheckResult(
        check_type="MissingDate",
        passed=len(bad) == 0,
        failed_records=len(bad),
        details=f"{len(bad)} records without a valid date" if bad else "",
    )
    return good, result


def check_date_out_of_range(records: list, valid_date_keys: set[int], date_attr: str = "trade_date"):
    """Drops records whose date isn't in the pre-seeded DimDate calendar.

    News feeds occasionally resurface old/republished articles with a stale
    `published` date (seen: an RSS entry dated 2021 showing up in a routine
    fetch); loading it would violate FactSentiment's DateKey FK. Rejected here
    rather than at the DB, matching the spec §8 pattern for bad dates.
    """
    good, bad = [], []
    for r in records:
        d = getattr(r, date_attr)
        key = int(d.strftime("%Y%m%d")) if d is not None else None
        (good if key in valid_date_keys else bad).append(r)
    result = CheckResult(
        check_type="DateOutOfRange",
        passed=len(bad) == 0,
        failed_records=len(bad),
        details=f"{len(bad)} records dated outside the seeded DimDate calendar dropped" if bad else "",
    )
    return good, result


def check_invalid_numeric_values(price_records: list):
    good, bad = [], []
    for r in price_records:
        if r.close is not None and r.close > 0 and r.volume is not None and r.volume >= 0:
            good.append(r)
        else:
            bad.append(r)
    result = CheckResult(
        check_type="InvalidNumericValues",
        passed=len(bad) == 0,
        failed_records=len(bad),
        details=f"negative/invalid price or volume dropped for: {[b.ticker for b in bad]}" if bad else "",
    )
    return good, result


def check_invalid_empty_headlines(news_records: list):
    good, bad = [], []
    for r in news_records:
        title = (r.title or "").strip()
        if title and any(ch.isprintable() for ch in title):
            good.append(r)
        else:
            bad.append(r)
    result = CheckResult(
        check_type="InvalidEmptyHeadlines",
        passed=len(bad) == 0,
        failed_records=len(bad),
        details=f"{len(bad)} empty/unreadable headlines dropped before scoring" if bad else "",
    )
    return good, result


def check_duplicate_news(news_records: list):
    seen = set()
    good, dup_count = [], 0
    for r in news_records:
        key = r.article_id
        if key in seen:
            dup_count += 1
            continue
        seen.add(key)
        good.append(r)
    result = CheckResult(
        check_type="DuplicateNews",
        passed=dup_count == 0,
        failed_records=dup_count,
        details=f"{dup_count} duplicate articles deduplicated" if dup_count else "",
    )
    return good, result


def check_duplicate_price_records(price_records: list):
    seen = set()
    good, dup_count = [], 0
    for r in price_records:
        key = (r.ticker, r.trade_date)
        if key in seen:
            dup_count += 1
            continue
        seen.add(key)
        good.append(r)
    result = CheckResult(
        check_type="DuplicatePriceRecords",
        passed=dup_count == 0,
        failed_records=dup_count,
        details=f"{dup_count} duplicate price rows deduplicated" if dup_count else "",
    )
    return good, result


def check_missing_sentiment_result(scored_articles: list):
    """Articles with score is None are NOT dropped — flagged and kept, per spec §8."""
    missing = [a for a in scored_articles if a.score is None]
    result = CheckResult(
        check_type="MissingSentimentResult",
        passed=len(missing) == 0,
        failed_records=len(missing),
        details=f"{len(missing)} articles flagged without a sentiment score (kept in NewsArticles)"
        if missing
        else "",
    )
    return result


def check_missing_closing_price(expected_ticker_dates: set[tuple[str, "date"]], present: set[tuple[str, "date"]]):
    """Compares trading-day x ticker combinations expected vs. what was actually loaded."""
    missing = expected_ticker_dates - present
    result = CheckResult(
        check_type="MissingClosingPrice",
        passed=len(missing) == 0,
        failed_records=len(missing),
        details=f"{len(missing)} ticker/trading-day combos missing a close price (holiday or feed gap possible)"
        if missing
        else "",
    )
    return result
