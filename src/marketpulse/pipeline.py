"""Orchestrates the full MarketPulse BI pipeline, spec §5 steps 1-9
(step 10, Power BI refresh, happens outside this process).

Run: python -m marketpulse.pipeline
"""
import logging
import uuid
from datetime import date

from sqlalchemy import text

from .config import settings
from .extract.news import fetch_news_status
from .extract.prices import fetch_prices
from .load.db import get_engine
from .load.freshness import mark_source, mark_stocks
from .load.upsert import get_asset_key_map, get_price_divisors, upsert_daily_prices, upsert_news_articles
from .logging_utils import configure_logging, log_stage, write_dq_results
from .recent_news import refresh_recent_news
from .quality.checks import (
    check_date_out_of_range,
    check_duplicate_news,
    check_duplicate_price_records,
    check_invalid_empty_headlines,
    check_invalid_numeric_values,
    check_missing_date,
    check_missing_ticker,
)
from .raw_storage import save_raw
from .transform import clean_headline
from .validation import is_valid_news, is_valid_price

log = logging.getLogger("pipeline")


def _get_watched_assets(conn) -> list[dict]:
    rows = conn.execute(text('SELECT "Ticker", "CompanyName", "Universe" FROM "DimAsset" ORDER BY "Ticker"')).all()
    return [{"ticker": t, "company_name": c, "universe": u} for t, c, u in rows]


def _get_valid_date_keys(conn) -> set[int]:
    rows = conn.execute(text('SELECT "DateKey" FROM "DimDate"')).all()
    return {row[0] for row in rows}


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
        valid_date_keys = _get_valid_date_keys(conn)
        divisors = get_price_divisors(conn)

        # --- Extract ---
        with log_stage(conn, run_id, "Extract") as stage_result:
            price_records, failed_price_tickers = fetch_prices(tickers, lookback_days=lookback_days, divisors=divisors)
            news_universes = {u.strip() for u in settings.news_universes.split(",") if u.strip()}
            news_records, news_reached = fetch_news_status([a for a in assets if a["universe"] in news_universes])
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
            price_records, dq_range = check_date_out_of_range(price_records, valid_date_keys)
            price_records, dq_numeric = check_invalid_numeric_values(price_records)
            price_records, dq_dup_price = check_duplicate_price_records(price_records)

            news_records, dq_news_ticker = check_missing_ticker(news_records, valid_tickers)
            news_records, dq_news_range = check_date_out_of_range(
                news_records, valid_date_keys, date_attr="published_at"
            )
            news_records, dq_empty = check_invalid_empty_headlines(news_records)
            news_records, dq_dup_news = check_duplicate_news(news_records)

            all_dq_results += [
                dq_ticker,
                dq_date,
                dq_range,
                dq_numeric,
                dq_dup_price,
                dq_news_ticker,
                dq_news_range,
                dq_empty,
                dq_dup_news,
            ]
            stage_result["records_processed"] = len(price_records) + len(news_records)

        # --- Load (idempotent) ---
        with log_stage(conn, run_id, "Load") as stage_result:
            asset_keys = get_asset_key_map(conn)
            n_prices = upsert_daily_prices(conn, price_records, asset_keys)
            n_news = upsert_news_articles(conn, news_records, asset_keys)
            # Latest headlines that explicitly name each ticker, from everything stored
            # in the lookback window (not just this run's fetch).
            n_news_items = refresh_recent_news(conn, list(asset_keys.values()))
            # Freshness is advanced only for tickers whose upstream fetch actually
            # worked, and only now that their rows are written.
            mark_stocks(conn, [asset_keys[t] for t in valid_tickers - set(failed_price_tickers) if t in asset_keys], "prices", "eod")
            mark_stocks(conn, [asset_keys[t] for t in news_reached if t in asset_keys], "news", "eod")
            if price_records:
                mark_source(conn, "prices")
            if news_reached:
                mark_source(conn, "news")
            conn.commit()
            log.info("recent-news panel refreshed: %d rows", n_news_items)
            stage_result["records_processed"] = n_prices + n_news

        # --- Data Quality Checks (persist results) ---
        with log_stage(conn, run_id, "DataQuality") as stage_result:
            write_dq_results(conn, run_id, all_dq_results)
            stage_result["records_processed"] = len(all_dq_results)

    log.info("=== pipeline run %s finished ===", run_id)
    return run_id


if __name__ == "__main__":
    run()
