"""FinBERT sentiment scoring for financial headlines (spec §9).

Loads ProsusAI/finbert once per process. Input: a headline. Output: one of
Positive/Negative/Neutral plus a confidence score. Text is truncated to the
model's max length; any inference failure is caught so a single bad headline
never aborts the pipeline (spec §10 FinBERT Processing Failure handling).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from functools import lru_cache

from ..config import settings

log = logging.getLogger(__name__)

MAX_TOKENS = 512


@dataclass
class SentimentResult:
    category: str  # Positive / Negative / Neutral
    score: float  # confidence of the winning category, 0..1


@lru_cache(maxsize=1)
def _get_pipeline():
    from transformers import pipeline

    return pipeline(
        "sentiment-analysis",
        model=settings.finbert_model_name,
        tokenizer=settings.finbert_model_name,
        truncation=True,
        max_length=MAX_TOKENS,
    )


def classify(headline: str) -> SentimentResult | None:
    """Returns None (not an exception) if scoring fails, per spec §8/§10:
    the article is kept but flagged with a missing sentiment result."""
    try:
        clf = _get_pipeline()
        out = clf(headline[: MAX_TOKENS * 4])[0]  # rough char cap before tokenizer truncation
        label = out["label"].strip().lower()
        category = {"positive": "Positive", "negative": "Negative", "neutral": "Neutral"}.get(
            label, "Neutral"
        )
        return SentimentResult(category=category, score=float(out["score"]))
    except Exception as exc:
        log.warning("FinBERT classification failed for headline %r: %s", headline[:80], exc)
        return None
