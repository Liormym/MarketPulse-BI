"""Per-stock technical/risk baselines: SMA-20/50/150/200, ATR-14 (plus its own
90-day rolling mean/stdev, for a relative volatility read), RSI-14, 20-day
average volume, and daily price gaps. Pure computation over price history
already sitting in FactDailyPrice - no new data fetch involved.

RSI is informational only (like the pattern hints): it is displayed on the
deep-dive page but is NOT an input to the Investment Score.

Daily updates are INCREMENTAL (see compute_and_upsert_technicals): a stock that
already has technicals only gets its newest bars (plus a small overlap that
picks up revised closes) recomputed, with INCREMENTAL_LOOKBACK_BARS of earlier
history as context, instead of its whole multi-year history. A stock with no
technicals yet gets one full baseline pass.

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
RSI_PERIOD = 14
RSI_OVERSOLD = 30.0  # below this reads as "oversold"
RSI_OVERBOUGHT = 70.0  # above this reads as "overbought"

# Incremental updates. The context in front of the first re-written bar must cover
# the longest look-back: SMA-200 needs 200 bars and the ATR-90 z-score needs 14 + 89.
# RSI is recursive (Wilder smoothing never forgets), but its memory decays by
# (13/14) per bar: after 250 bars of context the difference from a full-history
# run is ~1e-8 RSI points, far below the 1-decimal precision it is shown at.
INCREMENTAL_LOOKBACK_BARS = 250
INCREMENTAL_OVERLAP_BARS = 5  # bars that already have technicals but are recomputed anyway (revised closes)
UPSERT_CHUNK_ROWS = 2000


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
    rsi14: float | None = None


def _sma(values: list[float], period: int) -> float | None:
    if len(values) < period:
        return None
    return sum(values[-period:]) / period


def _sma_ending_at(values: list[float], end: int, period: int) -> float | None:
    """SMA of the `period` values ending at index `end` (inclusive), without
    copying the prefix: the same number _sma(values[:end + 1], period) gives."""
    if end + 1 < period:
        return None
    return sum(values[end + 1 - period : end + 1]) / period


def _rsi_from_averages(avg_gain: float, avg_loss: float) -> float | None:
    if avg_loss == 0:
        # Only gains -> 100. No movement at all -> RSI is undefined, not 50.
        return 100.0 if avg_gain > 0 else None
    return 100.0 - 100.0 / (1.0 + avg_gain / avg_loss)


def compute_rsi_series(closes: list[float], period: int = RSI_PERIOD) -> list[float | None]:
    """Wilder's RSI, one value per close (None until `period` price changes
    exist). The first average gain/loss is the plain mean of the first
    `period` changes; every later one is smoothed as
    (previous * (period - 1) + current) / period - the standard definition
    charting platforms use, so values line up with what traders see elsewhere."""
    rsi: list[float | None] = [None] * len(closes)
    if len(closes) <= period:
        return rsi

    changes = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains = [max(c, 0.0) for c in changes]
    losses = [max(-c, 0.0) for c in changes]

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    rsi[period] = _rsi_from_averages(avg_gain, avg_loss)

    for i in range(period, len(changes)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        rsi[i + 1] = _rsi_from_averages(avg_gain, avg_loss)
    return rsi


def describe_rsi(value: float | None) -> str | None:
    """"oversold" / "overbought" / "neutral", or None if there is no RSI."""
    if value is None:
        return None
    if value < RSI_OVERSOLD:
        return "oversold"
    if value > RSI_OVERBOUGHT:
        return "overbought"
    return "neutral"


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
    rsi_values = compute_rsi_series(closes)
    true_ranges: list[float | None] = []
    atr_history: list[float] = []  # non-None ATR14 readings seen so far, oldest-to-newest

    results = []
    for i, bar in enumerate(bars):
        prev_close = closes[i - 1] if i > 0 else None
        true_ranges.append(_true_range(bar, prev_close))

        smas = {p: _sma_ending_at(closes, i, p) for p in SMA_PERIODS}

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
                rsi14=rsi_values[i],
            )
        )
    return results


def _upsert_technicals(conn, asset_key: int, date_keys: list[int], technicals: list[DayTechnicals]) -> None:
    """Batched upsert (one multi-row INSERT per chunk instead of one round trip per row)."""
    from sqlalchemy import column, table
    from sqlalchemy.dialects.postgresql import insert

    cols = ("SMA20", "SMA50", "SMA150", "SMA200", "ATR14", "ATR90Avg", "ATR90Std", "AvgVolume20D", "GapPct", "RSI14")
    tbl = table("FactStockTechnicals", column("AssetKey"), column("DateKey"), *[column(c) for c in cols])
    rows = [
        {
            "AssetKey": asset_key,
            "DateKey": date_key,
            "SMA20": t.sma20,
            "SMA50": t.sma50,
            "SMA150": t.sma150,
            "SMA200": t.sma200,
            "ATR14": t.atr14,
            "ATR90Avg": t.atr_90d_avg,
            "ATR90Std": t.atr_90d_std,
            "AvgVolume20D": t.avg_volume_20d,
            "GapPct": t.gap_pct,
            "RSI14": t.rsi14,
        }
        for date_key, t in zip(date_keys, technicals)
    ]
    for i in range(0, len(rows), UPSERT_CHUNK_ROWS):
        stmt = insert(tbl).values(rows[i : i + UPSERT_CHUNK_ROWS])
        stmt = stmt.on_conflict_do_update(
            index_elements=["AssetKey", "DateKey"],
            set_={c: getattr(stmt.excluded, c) for c in cols},
        )
        conn.execute(stmt)


def compute_and_upsert_technicals(conn, asset_key: int, *, full: bool = False) -> tuple[int, str]:
    """Recomputes and upserts FactStockTechnicals for one asset. Returns
    (rows written, "baseline" | "incremental" | "none").

    baseline     the asset has no technicals yet, or its price history now
                 reaches back further than its technicals do (a backfill added
                 older bars), or full=True: every bar is computed.
    incremental  only the bars newer than the last computed one, plus
                 INCREMENTAL_OVERLAP_BARS already-computed bars, are written;
                 INCREMENTAL_LOOKBACK_BARS earlier bars are read as context.
                 Same numbers as a full run for every written bar (see
                 tests/test_technicals_incremental.py), at a fraction of the cost.
    """
    from sqlalchemy import text

    first_price, first_tech, last_tech = conn.execute(
        text(
            """
            SELECT (SELECT MIN("DateKey") FROM "FactDailyPrice" WHERE "AssetKey" = :a),
                   (SELECT MIN("DateKey") FROM "FactStockTechnicals" WHERE "AssetKey" = :a),
                   (SELECT MAX("DateKey") FROM "FactStockTechnicals" WHERE "AssetKey" = :a)
            """
        ),
        {"a": asset_key},
    ).one()
    if first_price is None:
        return 0, "none"

    baseline = full or last_tech is None or first_price < first_tech
    if baseline:
        rows = conn.execute(
            text(
                """
                SELECT "DateKey", "Open", "High", "Low", "Close", "Volume"
                FROM "FactDailyPrice" WHERE "AssetKey" = :a ORDER BY "DateKey" ASC
                """
            ),
            {"a": asset_key},
        ).all()
        write_n = len(rows)
    else:
        new_bars = conn.execute(
            text('SELECT COUNT(*) FROM "FactDailyPrice" WHERE "AssetKey" = :a AND "DateKey" > :last'),
            {"a": asset_key, "last": last_tech},
        ).scalar()
        write_n = new_bars + INCREMENTAL_OVERLAP_BARS
        rows = conn.execute(
            text(
                """
                SELECT "DateKey", "Open", "High", "Low", "Close", "Volume"
                FROM "FactDailyPrice" WHERE "AssetKey" = :a ORDER BY "DateKey" DESC LIMIT :n
                """
            ),
            {"a": asset_key, "n": write_n + INCREMENTAL_LOOKBACK_BARS},
        ).all()
        rows.reverse()

    date_keys = [r[0] for r in rows]
    bars = [DailyBar(open=r[1], high=r[2], low=r[3], close=r[4], volume=r[5]) for r in rows]
    technicals = compute_technicals(bars)

    write_n = min(write_n, len(rows))
    if write_n == 0:
        return 0, "none"
    _upsert_technicals(conn, asset_key, date_keys[-write_n:], technicals[-write_n:])

    from .load.freshness import mark_stocks

    mark_stocks(conn, [asset_key], "technicals")
    return write_n, "baseline" if baseline else "incremental"


def compute_and_upsert_technicals_for_asset(conn, asset_key: int, *, full: bool = False) -> int:
    """Rows written. Shared by scripts/compute_stock_technicals.py (all tickers,
    batch) and the live single-ticker refresh endpoint; incremental unless the
    asset has no technicals yet or full=True (see compute_and_upsert_technicals)."""
    return compute_and_upsert_technicals(conn, asset_key, full=full)[0]
