"""Aggregates scored articles into one FactSentiment row per asset per day (spec §5 step 6)."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date


@dataclass
class ScoredArticle:
    ticker: str
    trade_date: date
    category: str | None  # None if FinBERT scoring failed for this article
    score: float | None


@dataclass
class DailySentiment:
    ticker: str
    trade_date: date
    article_count: int
    positive_count: int
    negative_count: int
    avg_sentiment_score: float


def aggregate_daily_sentiment(articles: list[ScoredArticle]) -> list[DailySentiment]:
    groups: dict[tuple[str, date], list[ScoredArticle]] = defaultdict(list)
    for a in articles:
        groups[(a.ticker, a.trade_date)].append(a)

    results = []
    for (ticker, trade_date), group in groups.items():
        scored = [a for a in group if a.score is not None]
        positive = sum(1 for a in group if a.category == "Positive")
        negative = sum(1 for a in group if a.category == "Negative")
        avg_score = sum(a.score for a in scored) / len(scored) if scored else 0.0
        results.append(
            DailySentiment(
                ticker=ticker,
                trade_date=trade_date,
                article_count=len(group),
                positive_count=positive,
                negative_count=negative,
                avg_sentiment_score=avg_score,
            )
        )
    return results
