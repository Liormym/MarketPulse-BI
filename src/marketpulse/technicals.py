"""Per-stock technical/risk baselines: SMA-20/50/150/200, ATR-14 (plus its own
90-day rolling mean/stdev, for a relative volatility read), 20-day average
volume, and daily price gaps. Pure computation over price history already
sitting in FactDailyPrice - no new data fetch involved.

Gap detection returns a structured GapInfo (direction + the prev-close/open
price bounds), not just a bare percentage - see describe_gap(). This does
not attempt to detect whether a gap was later "filled" by subsequent price
action - that's a separate, more involved analysis not implemented here.
"""
from dataclasses import dataclass
from statistics import mean, stdev

SMA_PERIODS = (20, 50, 150, 200)
ATR_PERIOD = 14
AVG_VOLUME_WINDOW = 20
ATR_ZSCORE_WINDOW = 90  # trailing days of ATR14 readings used for the rolling mean/stdev


@dataclass
class DailyBar:
    open: float | None
    high: float | None
    low: float | None
    close: float
    volume: int


@dataclass
class GapInfo:
    direction: str  # "Up" or "Down"
    prev_close: float
    open: float
    gap_pct: float


@dataclass
class DayTechnicals:
    sma20: float | None
    sma50: float | None
    sma150: float | None
    sma200: float | None
    atr14: float | None
    atr_90d_avg: float | None
    atr_90d_std: float | None
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


def describe_gap(open_price: float | None, prev_close: float | None) -> GapInfo | None:
    """Structured opening-gap info: direction plus the exact prev-close/open
    price bounds, e.g. "Gap Up: $250.50 - $253.10" is
    GapInfo(direction="Up", prev_close=250.50, open=253.10, gap_pct=1.04).
    None if there's no prior close to compare against, or price didn't gap
    at all."""
    if open_price is None or not prev_close:
        return None
    gap_pct = (open_price - prev_close) / prev_close * 100
    if gap_pct == 0:
        return None
    return GapInfo(
        direction="Up" if gap_pct > 0 else "Down",
        prev_close=prev_close,
        open=open_price,
        gap_pct=gap_pct,
    )


def compute_technicals(bars: list[DailyBar]) -> list[DayTechnicals]:
    """bars ordered oldest-to-newest. Returns one DayTechnicals per input bar."""
    closes = [b.close for b in bars]
    volumes = [b.volume for b in bars]
    true_ranges: list[float | None] = []
    atr_history: list[float] = []  # non-None ATR14 readings seen so far, oldest-to-newest

    results = []
    for i, bar in enumerate(bars):
        prev_close = closes[i - 1] if i > 0 else None
        true_ranges.append(_true_range(bar, prev_close))

        closes_so_far = closes[: i + 1]
        smas = {p: _sma(closes_so_far, p) for p in SMA_PERIODS}

        tr_window = [tr for tr in true_ranges[-ATR_PERIOD:] if tr is not None]
        atr = sum(tr_window) / len(tr_window) if len(tr_window) == ATR_PERIOD else None

        atr_90d_avg = None
        atr_90d_std = None
        if atr is not None:
            atr_history.append(atr)
            zscore_window = atr_history[-ATR_ZSCORE_WINDOW:]
            if len(zscore_window) == ATR_ZSCORE_WINDOW:
                atr_90d_avg = mean(zscore_window)
                atr_90d_std = stdev(zscore_window)

        vol_window = volumes[max(0, i - AVG_VOLUME_WINDOW) : i]
        avg_volume = sum(vol_window) / len(vol_window) if len(vol_window) == AVG_VOLUME_WINDOW else None

        gap = describe_gap(bar.open, prev_close)
        gap_pct = gap.gap_pct if gap is not None else None

        results.append(
            DayTechnicals(
                sma20=smas[20],
                sma50=smas[50],
                sma150=smas[150],
                sma200=smas[200],
                atr14=atr,
                atr_90d_avg=atr_90d_avg,
                atr_90d_std=atr_90d_std,
                avg_volume_20d=avg_volume,
                gap_pct=gap_pct,
            )
        )
    return results


def compute_and_upsert_technicals_for_asset(conn, asset_key: int) -> int:
    """Recomputes and upserts FactStockTechnicals for one asset from its full
    FactDailyPrice history. Shared by scripts/compute_stock_technicals.py
    (all tickers, batch) and the live single-ticker refresh endpoint.
    """
    from sqlalchemy import text  # local import: keeps this module DB-free for pure-function callers/tests

    rows = conn.execute(
        text(
            """
            SELECT d."DateKey", p."Open", p."High", p."Low", p."Close", p."Volume"
            FROM "FactDailyPrice" p
            JOIN "DimDate" d ON d."DateKey" = p."DateKey"
            WHERE p."AssetKey" = :asset_key
            ORDER BY d."Date" ASC
            """
        ),
        {"asset_key": asset_key},
    ).all()
    if not rows:
        return 0

    date_keys = [r[0] for r in rows]
    bars = [DailyBar(open=r[1], high=r[2], low=r[3], close=r[4], volume=r[5]) for r in rows]
    technicals = compute_technicals(bars)

    for date_key, t in zip(date_keys, technicals):
        conn.execute(
            text(
                """
                INSERT INTO "FactStockTechnicals"
                    ("AssetKey", "DateKey", "SMA20", "SMA50", "SMA150", "SMA200",
                     "ATR14", "ATR90Avg", "ATR90Std", "AvgVolume20D", "GapPct")
                VALUES (:asset_key, :date_key, :sma20, :sma50, :sma150, :sma200,
                        :atr14, :atr_90d_avg, :atr_90d_std, :avg_volume, :gap_pct)
                ON CONFLICT ("AssetKey", "DateKey") DO UPDATE
                    SET "SMA20" = EXCLUDED."SMA20", "SMA50" = EXCLUDED."SMA50",
                        "SMA150" = EXCLUDED."SMA150", "SMA200" = EXCLUDED."SMA200",
                        "ATR14" = EXCLUDED."ATR14", "ATR90Avg" = EXCLUDED."ATR90Avg",
                        "ATR90Std" = EXCLUDED."ATR90Std", "AvgVolume20D" = EXCLUDED."AvgVolume20D",
                        "GapPct" = EXCLUDED."GapPct"
                """
            ),
            {
                "asset_key": asset_key,
                "date_key": date_key,
                "sma20": t.sma20,
                "sma50": t.sma50,
                "sma150": t.sma150,
                "sma200": t.sma200,
                "atr14": t.atr14,
                "atr_90d_avg": t.atr_90d_avg,
                "atr_90d_std": t.atr_90d_std,
                "avg_volume": t.avg_volume_20d,
                "gap_pct": t.gap_pct,
            },
        )
    return len(rows)
