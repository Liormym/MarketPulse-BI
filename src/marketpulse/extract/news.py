"""Pulls news headlines per ticker from free RSS feeds (no API key, $0 cost per spec §2.2.2).

Primary source: Yahoo Finance's per-ticker RSS feed. Falls back to a Google News
RSS search by ticker symbol if Yahoo returns nothing, since RSS feeds can be
flaky or thin for smaller tickers.

Tickers are fetched concurrently (bounded by settings.news_max_workers), and
every feed request goes through the shared outbound rate limiter.
"""
from __future__ import annotations

import hashlib
import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import quote_plus

import feedparser
import requests
from tenacity import retry, stop_after_attempt, wait_exponential

from ..config import settings
from ..news_relevance import company_name_for_search, mentions_stock, normalize_title
from .ratelimit import OUTBOUND_LIMITER

log = logging.getLogger(__name__)

YAHOO_RSS_URL = "https://feeds.finance.yahoo.com/rss/2.0/headline?s={ticker}&region=US&lang=en-US"
GOOGLE_NEWS_RSS_URL = "https://news.google.com/rss/search?q={query}&hl=en-US&gl=US&ceid=US:en"

# feedparser.parse(url) has no built-in network timeout and can hang
# indefinitely on a slow/unresponsive feed. Fetch bytes ourselves with a hard
# timeout, then hand them to feedparser to parse — never fetch by URL directly.
REQUEST_TIMEOUT_SECONDS = 10
USER_AGENT = "MarketPulseBI/1.0 (+data pipeline; see spec doc for contact)"


@dataclass
class NewsRecord:
    article_id: str
    ticker: str
    published_at: datetime
    title: str
    source_url: str


def _article_id(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()


def _parse_published(entry) -> datetime:
    if getattr(entry, "published", None):
        try:
            return parsedate_to_datetime(entry.published)
        except (TypeError, ValueError):
            pass
    return datetime.now(timezone.utc)


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=10))
def _parse_feed(url: str):
    OUTBOUND_LIMITER.wait()
    try:
        resp = requests.get(
            url,
            timeout=REQUEST_TIMEOUT_SECONDS,
            headers={"User-Agent": USER_AGENT},
        )
        resp.raise_for_status()
    except requests.RequestException as exc:
        raise ConnectionError(f"failed to fetch feed: {url}: {exc}") from exc

    feed = feedparser.parse(resp.content)
    if feed.bozo and not feed.entries:
        raise ConnectionError(f"failed to parse feed: {url}")
    return feed


def _entries_to_records(feed, ticker: str, limit: int) -> list[NewsRecord]:
    records = []
    for entry in feed.entries[:limit]:
        link = getattr(entry, "link", None)
        title = getattr(entry, "title", None)
        if not link or not title:
            continue
        records.append(
            NewsRecord(
                article_id=_article_id(link),
                ticker=ticker,
                published_at=_parse_published(entry),
                title=title.strip(),
                source_url=link,
            )
        )
    return records


def _feed_urls(ticker: str, company_name: str) -> list[str]:
    """Yahoo's per-ticker feed is broad (lots of market-wide pieces); a Google
    News search on the quoted company name is much more on-topic, so both are
    always queried. Google's ticker search is a last resort."""
    name = quote_plus(f'"{company_name_for_search(company_name)}" stock')
    return [
        YAHOO_RSS_URL.format(ticker=ticker),
        GOOGLE_NEWS_RSS_URL.format(query=name),
        GOOGLE_NEWS_RSS_URL.format(query=f"{ticker}+stock"),
    ]


def fetch_news_for_ticker_status(ticker: str, company_name: str, limit: int) -> tuple[list[NewsRecord], bool]:
    """Newest-first headlines that are actually ABOUT the stock (see
    news_relevance.mentions_stock); everything else the feeds return is dropped here,
    before it reaches the database.

    Also returns whether at least one feed was reached. "Reached, nothing
    relevant" and "every feed failed" both give an empty list, but only the
    first may advance a ticker's news-freshness timestamp."""
    any_feed_ok = False
    seen: dict[str, NewsRecord] = {}
    seen_titles: set[str] = set()  # the same story arrives via Yahoo and Google under different URLs

    for i, url in enumerate(_feed_urls(ticker, company_name)):
        if i == 2 and seen:
            break  # the ticker-only Google search is only a fallback
        try:
            feed = _parse_feed(url)
        except Exception as exc:
            log.warning("news feed failed for %s (%s): %s", ticker, url, exc)
            continue
        any_feed_ok = True
        for record in _entries_to_records(feed, ticker, limit * 3):
            key = normalize_title(record.title)
            if record.article_id in seen or key in seen_titles:
                continue
            if mentions_stock(record.title, ticker, company_name):
                seen[record.article_id] = record
                seen_titles.add(key)

    return sorted(seen.values(), key=lambda r: r.published_at, reverse=True)[:limit], any_feed_ok


def fetch_news_for_ticker(ticker: str, company_name: str, limit: int) -> list[NewsRecord]:
    return fetch_news_for_ticker_status(ticker, company_name, limit)[0]


def _fetch_ticker_news(asset: dict, limit: int) -> tuple[list[NewsRecord], bool]:
    try:
        return fetch_news_for_ticker_status(asset["ticker"], asset["company_name"], limit)
    except Exception:
        log.exception("news fetch crashed for %s; skipping this ticker", asset["ticker"])
        return [], False


def fetch_news_status(assets: list[dict], limit_per_ticker: int | None = None) -> tuple[list[NewsRecord], set[str]]:
    """assets: list of {'ticker': ..., 'company_name': ...} from DimAsset.
    Returns (records, tickers whose feeds were actually reached)."""
    limit = limit_per_ticker or settings.news_per_ticker_limit
    with ThreadPoolExecutor(max_workers=settings.news_max_workers) as pool:
        per_ticker = list(pool.map(lambda asset: _fetch_ticker_news(asset, limit), assets))
    records = [record for recs, _ in per_ticker for record in recs]
    reached = {asset["ticker"] for asset, (_, ok) in zip(assets, per_ticker) if ok}
    return records, reached


def fetch_news(assets: list[dict], limit_per_ticker: int | None = None) -> list[NewsRecord]:
    return fetch_news_status(assets, limit_per_ticker)[0]
