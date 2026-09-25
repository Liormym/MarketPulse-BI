"""Per-stock technical/risk baselines: SMA-20/50/150/200, ATR-14, 20-day
average volume, and daily price gaps. Pure computation over price history
already sitting in FactDailyPrice - no new data fetch involved.

Gap % is the raw opening gap (today's Open vs. yesterday's Close); this does
not attempt to detect whether a gap was later "filled" by subsequent price
action - that's a separate, more involved analysis not implemented here.
"""
from dataclasses import dataclass

SMA_PERIODS = (20, 50, 150, 200)
ATR_PERIOD = 14
AVG_VOLUME_WINDOW = 20


@dataclass
class DailyBar:
    open: float | None
    high: float | None
    low: float | None
    close: float
    volume: int


@dataclass
class DayTechnicals:
    sma20: float | None
    sma50: float | None
    sma150: float | None
    sma200: float | None
    atr14: float | None
    avg_volume_20d: float | None
    gap_pct: float | None


def _sma(values: list[float], period: int) -> float | None:
    if len(values) < period:
        return None
    return sum(values[-period:]) / period


def _true_range(bar: DailyBar, prev_close: float | None) -> float | None:
    if bar.high is None or bar.low is None:
        return None
    tr = bar.high - bar.low
    if prev_close is not None:
        tr = max(tr, abs(bar.high - prev_close), abs(bar.low - prev_close))
    return tr


def compute_technicals(bars: list[DailyBar]) -> list[DayTechnicals]:
    """bars ordered oldest-to-newest. Returns one DayTechnicals per input bar."""
    closes = [b.close for b in bars]
    volumes = [b.volume for b in bars]
    true_ranges: list[float | None] = []

    results = []
    for i, bar in enumerate(bars):
        prev_close = closes[i - 1] if i > 0 else None
        true_ranges.append(_true_range(bar, prev_close))

        closes_so_far = closes[: i + 1]
        smas = {p: _sma(closes_so_far, p) for p in SMA_PERIODS}

        tr_window = [tr for tr in true_ranges[-ATR_PERIOD:] if tr is not None]
        atr = sum(tr_window) / len(tr_window) if len(tr_window) == ATR_PERIOD else None

        vol_window = volumes[max(0, i - AVG_VOLUME_WINDOW) : i]
        avg_volume = sum(vol_window) / len(vol_window) if len(vol_window) == AVG_VOLUME_WINDOW else None

        gap_pct = None
        if bar.open is not None and prev_close:
            gap_pct = (bar.open - prev_close) / prev_close * 100

        results.append(
            DayTechnicals(
                sma20=smas[20],
                sma50=smas[50],
                sma150=smas[150],
                sma200=smas[200],
                atr14=atr,
                avg_volume_20d=avg_volume,
                gap_pct=gap_pct,
            )
        )
    return results
