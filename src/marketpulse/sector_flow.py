"""Sector money-flow classification for the 11 S&P 500 Sector SPDR ETFs.

Volume Ratio = today's volume / 20-day average volume. A ratio above the
threshold on an up day suggests institutional buying (Accumulation); the
same ratio on a down day suggests selling (Distribution). Otherwise Neutral.
The threshold is a documented heuristic, not a backtested constant, same
spirit as the calibration constants in scoring.py - easy to retune.
"""
from dataclasses import dataclass

SECTOR_SPDR_TICKERS = [
    "XLK", "XLF", "XLE", "XLV", "XLY", "XLP", "XLI", "XLB", "XLU", "XLRE", "XLC",
]

# DimAsset.Sector (this project's naming, see config/watchlist.yaml) -> the
# SPDR ETF tracking that GICS sector. Sectors outside GICS (ETF, Index,
# Crypto - from the original personal-holdings list) intentionally have no
# entry: there's no sector money-flow signal for those.
SECTOR_TO_SPDR = {
    "Technology": "XLK",
    "Financials": "XLF",
    "Energy": "XLE",
    "Healthcare": "XLV",
    "Consumer Discretionary": "XLY",
    "Consumer Staples": "XLP",
    "Industrials": "XLI",
    "Materials": "XLB",
    "Utilities": "XLU",
    "Real Estate": "XLRE",
    "Communication Services": "XLC",
}

AVG_VOLUME_WINDOW = 20
VOLUME_RATIO_THRESHOLD = 1.5


@dataclass
class DayFlow:
    avg_volume_20d: float | None
    dollar_volume: float
    volume_ratio: float | None
    flow_status: str | None  # Accumulation / Distribution / Neutral; None if not enough history


def classify_flow(volume_ratio: float | None, price_change: float | None) -> str | None:
    if volume_ratio is None or price_change is None:
        return None
    if volume_ratio > VOLUME_RATIO_THRESHOLD:
        if price_change > 0:
            return "Accumulation"
        if price_change < 0:
            return "Distribution"
    return "Neutral"


def compute_daily_flows(closes: list[float], volumes: list[int]) -> list[DayFlow]:
    """closes/volumes ordered oldest-to-newest, equal length. Returns one
    DayFlow per input day (same length, same order)."""
    if len(closes) != len(volumes):
        raise ValueError("closes and volumes must be the same length")

    results = []
    for i in range(len(closes)):
        # Trailing window of the PRIOR AVG_VOLUME_WINDOW days, excluding today -
        # today's volume is what we're comparing against the baseline, not part of it.
        window = volumes[max(0, i - AVG_VOLUME_WINDOW) : i]
        avg_volume = sum(window) / len(window) if len(window) == AVG_VOLUME_WINDOW else None
        dollar_volume = closes[i] * volumes[i]
        ratio = volumes[i] / avg_volume if avg_volume else None
        price_change = closes[i] - closes[i - 1] if i > 0 else None
        status = classify_flow(ratio, price_change)
        results.append(DayFlow(avg_volume, dollar_volume, ratio, status))
    return results
