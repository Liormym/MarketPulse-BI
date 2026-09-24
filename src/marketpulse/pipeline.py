"""Orchestrates the full MarketPulse BI pipeline, spec §5 steps 1-9
(step 10, Power BI refresh, happens outside this process).

Run: python -m marketpulse.pipeline
"""
import logging
import uuid
from datetime import date

from sqlalchemy import text

from .aggregate import ScoredArticle, aggregate_daily_sentiment
from .extract.news import fetch_news
from .extract.prices import fetch_prices
from .load.db import get_engine
from .load.upsert import get_asset_key_map, upsert_daily_prices, upsert_daily_sentiment, upsert_news_articles
from .logging_utils import configure_logging, log_stage, write_dq_results
from .quality.checks import (
    check_duplicate_news,
    check_duplicate_price_records,
    check_invalid_empty_headlines,
    check_invalid_numeric_values,
    check_missing_date,
    check_missing_sentiment_result,
    check_missing_ticker,
)
from .raw_storage import save_raw
from .sentiment.finbert import classify
from .transform import clean_headline
from .validation import is_valid_news, is_valid_price

log = logging.getLogger("pipeline")


def _get_watched_assets(conn) -> list[dict]:
    rows = conn.execute(text('SELECT "Ticker", "CompanyName" FROM "DimAsset" ORDER BY "Ticker"')).all()
    return [{"ticker": t, "company_name": c} for t, c in rows]


def run(lookback_days: int = 5) -> uuid.UUID:
    configure_logging()
    run_id = uuid.uuid4()
    today = date.today()
    engine = get_engine()
    all_dq_results = []

    log.info("=== pipeline run %s starting ===", run_id)

    with engine.connect() as conn:
        assets = _get_watched_assets(conn)
        valid_tickers = {a["ticker"] for a in assets}
        tickers = list(valid_tickers)

        # --- Extract ---
        with log_stage(conn, run_id, "Extract") as stage_result:
            price_records, failed_price_tickers = fetch_prices(tickers, lookback_days=lookback_days)
            news_records = fetch_news(assets)
            save_raw(today, "prices", [r.__dict__ for r in price_records])
            save_raw(today, "news", [r.__dict__ for r in news_records])
            stage_result["records_processed"] = len(price_records) + len(news_records)
            if failed_price_tickers:
                log.warning("price fetch failed for tickers: %s", failed_price_tickers)

        # --- Validate + Transform ---
        with log_stage(conn, run_id, "Transform") as stage_result:
            price_records = [r for r in price_records if is_valid_price(r)]
            news_records = [r for r in news_records if is_valid_news(r)]
            for r in news_records:
                r.title = clean_headline(r.title)

            price_records, dq_ticker = check_missing_ticker(price_records, valid_tickers)
            price_records, dq_date = check_missing_date(price_records)
            price_records, dq_numeric = check_invalid_numeric_values(price_records)
            price_records, dq_dup_price = check_duplicate_price_records(price_records)

            news_records, dq_news_ticker = check_missing_ticker(news_records, valid_tickers)
            news_records, dq_empty = check_invalid_empty_headlines(news_records)
            news_records, dq_dup_news = check_duplicate_news(news_records)

            all_dq_results += [
                dq_ticker,
                dq_date,
                dq_numeric,
                dq_dup_price,
                dq_news_ticker,
                dq_empty,
                dq_dup_news,
            ]
            stage_result["records_processed"] = len(price_records) + len(news_records)

        # --- Sentiment Analysis ---
        with log_stage(conn, run_id, "AI") as stage_result:
            scored_articles = []
            for r in news_records:
                result = classify(r.title)
                r.category = result.category if result else None
                r.score = result.score if result else None
                scored_articles.append(
                    ScoredArticle(
                        ticker=r.ticker,
                        trade_date=r.published_at.date(),
                        category=r.category,
                        score=r.score,
                    )
                )
            dq_missing_sentiment = check_missing_sentiment_result(scored_articles)
            all_dq_results.append(dq_missing_sentiment)
            stage_result["records_processed"] = len(scored_articles)

        # --- Aggregation ---
        with log_stage(conn, run_id, "Aggregate") as stage_result:
            daily_sentiment = aggregate_daily_sentiment(scored_articles)
            stage_result["records_processed"] = len(daily_sentiment)

        # --- Load (idempotent) ---
        with log_stage(conn, run_id, "Load") as stage_result:
            asset_keys = get_asset_key_map(conn)
            n_prices = upsert_daily_prices(conn, price_records, asset_keys)
            n_news = upsert_news_articles(conn, news_records, asset_keys)
            n_sentiment = upsert_daily_sentiment(conn, daily_sentiment, asset_keys)
            conn.commit()
            stage_result["records_processed"] = n_prices + n_news + n_sentiment

        # --- Data Quality Checks (persist results) ---
        with log_stage(conn, run_id, "DataQuality") as stage_result:
            write_dq_results(conn, run_id, all_dq_results)
            stage_result["records_processed"] = len(all_dq_results)

    log.info("=== pipeline run %s finished ===", run_id)
    return run_id


if __name__ == "__main__":
    run()
