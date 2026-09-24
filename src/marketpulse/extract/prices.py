"""Pulls daily OHLCV data from yfinance for the watchlist.

Handles rate limiting per spec §10: on 429/timeout, retries up to 3 times with
exponential backoff; if a ticker still fails, it's logged and skipped so the
rest of the pipeline continues (never a fatal error for a single ticker).
"""
import logging
import time
from dataclasses import dataclass
from datetime import date, timedelta

import yfinance as yf
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from ..config import settings

log = logging.getLogger(__name__)


@dataclass
class PriceRecord:
    ticker: str
    trade_date: date
    close: float
    volume: int


class TickerFetchError(Exception):
    pass


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=10),
    retry=retry_if_exception_type(TickerFetchError),
    reraise=True,
)
def _fetch_one(ticker: str, lookback_days: int):
    try:
        hist = yf.Ticker(ticker).history(period=f"{lookback_days}d", interval="1d", timeout=10)
    except Exception as exc:  # network/rate-limit errors surface as generic exceptions from yfinance
        raise TickerFetchError(f"{ticker}: {exc}") from exc
    if hist is None or hist.empty:
        raise TickerFetchError(f"{ticker}: empty response")
    return hist


def fetch_prices(tickers: list[str], lookback_days: int = 5) -> tuple[list[PriceRecord], list[str]]:
    """Returns (records, failed_tickers). Never raises for individual ticker failures."""
    records: list[PriceRecord] = []
    failed: list[str] = []

    for ticker in tickers:
        try:
            hist = _fetch_one(ticker, lookback_days)
        except TickerFetchError as exc:
            log.warning("giving up on %s after retries: %s", ticker, exc)
            failed.append(ticker)
            continue

        for idx, row in hist.iterrows():
            records.append(
                PriceRecord(
                    ticker=ticker,
                    trade_date=idx.date(),
                    close=float(row["Close"]),
                    volume=int(row["Volume"]),
                )
            )
        time.sleep(settings.request_throttle_seconds)

    return records, failed
