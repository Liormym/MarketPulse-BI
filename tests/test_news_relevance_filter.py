"""Fetching drops headlines that are not about the stock, before they reach the DB."""
from types import SimpleNamespace

from marketpulse.extract import news


def _feed(*titles):
    return SimpleNamespace(
        entries=[
            SimpleNamespace(title=t, link=f"https://example.com/{i}", published="Wed, 07 Oct 2026 12:00:00 GMT")
            for i, t in enumerate(titles)
        ]
    )


def test_off_topic_headlines_are_dropped_and_sources_merged(monkeypatch):
    feeds = {
        "yahoo": _feed("Nasdaq sinks 345 points as tech slides", "Chevron raises dividend"),
        "google": _feed("Chevron CEO warns on diesel export ban", "Sector Update: Energy Stocks Fall", "Chevron raises dividend"),
    }

    def fake_parse(url):
        return feeds["yahoo" if "yahoo" in url else "google"]

    monkeypatch.setattr(news, "_parse_feed", fake_parse)
    out = news.fetch_news_for_ticker("CVX", "Chevron Corporation", 15)

    titles = sorted(r.title for r in out)
    assert titles == ["Chevron CEO warns on diesel export ban", "Chevron raises dividend"]  # no roundups, no dupes
