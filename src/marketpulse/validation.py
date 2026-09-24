"""Basic type/required-field checks, run before transformation (spec §5 step 3)."""
from .extract.news import NewsRecord
from .extract.prices import PriceRecord


def is_valid_price(rec: PriceRecord) -> bool:
    return (
        rec.ticker is not None
        and rec.trade_date is not None
        and rec.close is not None
        and rec.close > 0
        and rec.volume is not None
        and rec.volume >= 0
    )


def is_valid_news(rec: NewsRecord) -> bool:
    return (
        rec.article_id is not None
        and rec.ticker is not None
        and rec.published_at is not None
        and rec.title is not None
        and len(rec.title.strip()) > 0
    )
