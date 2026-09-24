"""Pulls news headlines per ticker from free RSS feeds (no API key, $0 cost per spec §2.2.2).

Primary source: Yahoo Finance's per-ticker RSS feed. Falls back to a Google News
RSS search by ticker symbol if Yahoo returns nothing, since RSS feeds can be
flaky or thin for smaller tickers.
"""
from __future__ import annotations

import hashlib
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import feedparser
import requests
from tenacity import retry, stop_after_attempt, wait_exponential

from ..config import settings

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
    category: str | None = None  # filled in by the sentiment stage
    score: float | None = None


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


def fetch_news_for_ticker(ticker: str, company_name: str, limit: int) -> list[NewsRecord]:
    records: list[NewsRecord] = []

    for url in (
        YAHOO_RSS_URL.format(ticker=ticker),
        GOOGLE_NEWS_RSS_URL.format(query=f"{ticker}+stock"),
    ):
        try:
            feed = _parse_feed(url)
        except Exception as exc:
            log.warning("news feed failed for %s (%s): %s", ticker, url, exc)
            continue

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
        if records:
            break  # Yahoo produced results, skip the Google fallback

    return records[:limit]


def fetch_news(assets: list[dict], limit_per_ticker: int | None = None) -> list[NewsRecord]:
    """assets: list of {'ticker': ..., 'company_name': ...} from DimAsset."""
    limit = limit_per_ticker or settings.news_per_ticker_limit
    all_records: list[NewsRecord] = []
    for asset in assets:
        recs = fetch_news_for_ticker(asset["ticker"], asset["company_name"], limit)
        all_records.extend(recs)
        time.sleep(settings.request_throttle_seconds)
    return all_records
