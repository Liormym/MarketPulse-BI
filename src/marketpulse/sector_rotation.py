"""Early Sector Rotation Engine: a fast read on where capital is moving between
the 11 US sectors, so a sector that is bleeding (or surging) shows up in the
score within days instead of lagging for weeks behind a 20-day trend.

Three inputs per sector, all relative to the S&P 500 EQUAL WEIGHT ETF (RSP, the
series already in MacroIndicators.RSPPrice):

    2-day relative return    sector ETF return minus the benchmark return, in
    5-day relative return    percentage points, over the last 2 / 5 trading bars
    breadth                  % of the sector's S&P 1500 stocks that rose on the
                             day (advancers / (advancers + decliners)), measured
                             AGAINST THE CROSS-SECTOR MEDIAN breadth (see below)

plus the 20-day relative return (the medium-term trend, kept for the score).

WHY EQUAL WEIGHT, NOT THE CAP-WEIGHTED S&P 500 (or SPY). A cap-weighted index is a
handful of mega-caps. When they fall, almost every OTHER sector "beats the index" by
simply falling less, which reads as capital inflow that isn't there. Measured on real
data (2026-11-04): against the S&P 500 the average sector showed +2.6pp over 5 days and
+6.1pp over 20, with 9 of 11 sectors "leading"; against RSP the average is -0.2pp / -0.7pp
(7 and 6 of 11), i.e. the signal is sector flow, not mega-cap drag. A cross-sector median
was rejected: it forces a fixed ~5/11 split every day and is skewed (mean -1.5pp).

WHY BREADTH IS RELATIVE. Raw breadth is an absolute gate: on a day when 85-95% of all
stocks rise, almost every sector clears "more advancers than decliners" and the engine
calls six of eleven sectors an inflow, which is a market-wide up day, not a rotation.
Rotation is a RELATIVE move, so breadth is taken as the sector's advancing share minus the
median advancing share across the sectors that day (a sector at 70% on a day the median is
85% is lagging in breadth). The raw percentage is still stored and shown.

rotation_signal() blends the three short-term inputs into one number from -1
(aggressive outflow) to +1 (aggressive inflow) and, only when all three AGREE,
names a regime: "inflow" or "outflow". Agreement is the point: a single bad day
(or a breadth wobble) is noise; a sector that lags the index over 2 days AND 5
days AND has more decliners than advancers is leaving, and the score takes an
explicit deduction for it (see scoring.py).

Every band, weight and threshold below is a documented calibration heuristic,
NOT a fitted or backtested parameter, in the same spirit as scoring.py. Nothing
here claims predictive power; it is a faster diagnostic of where money is
moving today.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from .sector_flow import SECTOR_SPDR_TICKERS, SECTOR_TO_SPDR

BARS_2D, BARS_5D, BARS_20D = 2, 5, 20

# A relative move of this size (percentage points vs the index) is a full-strength signal.
ROTATION_BAND_2D_PP = 1.5
ROTATION_BAND_5D_PP = 3.0
# Breadth this many points away from 50% advancing is a full-strength signal.
ROTATION_BAND_BREADTH_PP = 25.0  # absolute fallback (no cross-sector median available)
# Breadth this many points above/below the cross-sector median is a full-strength signal.
ROTATION_BAND_BREADTH_VS_MEDIAN_PP = 20.0
ROTATION_WEIGHTS = {"rel_2d": 0.35, "rel_5d": 0.40, "breadth": 0.25}
REGIME_SCORE_THRESHOLD = 0.35  # |signal| needed, on top of the agreement rules, to name a regime
MIN_BREADTH_STOCKS = 5  # below this many movers the breadth percentage is too noisy to use
MIN_SECTORS_FOR_MEDIAN = 5  # fewer sectors with a breadth read than this: no median, absolute 50% centre

REGIME_INFLOW, REGIME_OUTFLOW, REGIME_BALANCED = "inflow", "outflow", "balanced"
REGIME_LABELS = {
    REGIME_INFLOW: "Inflow / Leading",
    REGIME_OUTFLOW: "Outflow / Lagging",
    REGIME_BALANCED: "Balanced",
}
BREADTH_UNIVERSES = ("sp500", "sp400", "sp600")  # the S&P 1500; TASE has no sector ETF
BENCHMARK_COLUMN = "RSPPrice"  # MacroIndicators column holding the benchmark's daily close
BENCHMARK_LABEL = "S&P 500 Equal Weight (RSP)"


@dataclass
class RotationReading:
    score: float | None  # -1 .. +1; None when the relative returns are unavailable
    regime: str | None  # inflow | outflow | balanced; None when score is None


def _clamp(x: float, low: float, high: float) -> float:
    return max(low, min(high, x))


def breadth_median(breadths) -> float | None:
    """Median advancing share across the sectors that have a breadth read; None
    when too few sectors do (the signal then falls back to the absolute 50% centre)."""
    vals = sorted(b for b in breadths if b is not None)
    if len(vals) < MIN_SECTORS_FOR_MEDIAN:
        return None
    mid = len(vals) // 2
    return vals[mid] if len(vals) % 2 else (vals[mid - 1] + vals[mid]) / 2.0


def rotation_signal(
    rel_2d_pp: float | None,
    rel_5d_pp: float | None,
    breadth_pct: float | None,
    breadth_median_pct: float | None = None,
) -> RotationReading:
    """Blend the short-term inputs into -1..+1 and name the regime.

    Both relative returns are required (without them there is no read at all).
    Breadth is optional: when missing the other two are re-weighted, but then a
    regime can never be named, because breadth is part of the definition of a
    rotation. With `breadth_median_pct` the breadth leg is the sector's advancing
    share relative to the cross-sector median (the rotation read); without it the
    leg is relative to an absolute 50%."""
    if rel_2d_pp is None or rel_5d_pp is None:
        return RotationReading(None, None)

    if breadth_pct is None:
        breadth_edge = None
        band = ROTATION_BAND_BREADTH_PP
    elif breadth_median_pct is None:
        breadth_edge = breadth_pct - 50.0
        band = ROTATION_BAND_BREADTH_PP
    else:
        breadth_edge = breadth_pct - breadth_median_pct
        band = ROTATION_BAND_BREADTH_VS_MEDIAN_PP

    parts = [
        (ROTATION_WEIGHTS["rel_2d"], _clamp(rel_2d_pp / ROTATION_BAND_2D_PP, -1.0, 1.0)),
        (ROTATION_WEIGHTS["rel_5d"], _clamp(rel_5d_pp / ROTATION_BAND_5D_PP, -1.0, 1.0)),
    ]
    if breadth_edge is not None:
        parts.append((ROTATION_WEIGHTS["breadth"], _clamp(breadth_edge / band, -1.0, 1.0)))
    total_weight = sum(w for w, _ in parts)
    score = sum(w * s for w, s in parts) / total_weight

    regime = REGIME_BALANCED
    if breadth_edge is not None:
        if score <= -REGIME_SCORE_THRESHOLD and rel_2d_pp < 0 and rel_5d_pp < 0 and breadth_edge < 0:
            regime = REGIME_OUTFLOW
        elif score >= REGIME_SCORE_THRESHOLD and rel_2d_pp > 0 and rel_5d_pp > 0 and breadth_edge > 0:
            regime = REGIME_INFLOW
    return RotationReading(round(score, 4), regime)


def align_closes(
    sector: list[tuple[int, float]], benchmark: list[tuple[int, float]]
) -> tuple[list[int], list[float], list[float]]:
    """Keeps only the DateKeys present in BOTH series (oldest first), so an
    n-bar return always compares the same calendar days for the sector and the
    index even when one of them is missing a day."""
    bench = dict(benchmark)
    dates, s_closes, b_closes = [], [], []
    for dk, close in sector:
        if dk in bench and close and bench[dk]:
            dates.append(dk)
            s_closes.append(close)
            b_closes.append(bench[dk])
    return dates, s_closes, b_closes


def relative_return_pp(s_closes: list[float], b_closes: list[float], bars: int) -> float | None:
    """Sector n-bar return minus index n-bar return, in percentage points, over
    the latest `bars` aligned bars. None when there is not enough history."""
    if len(s_closes) <= bars or len(b_closes) <= bars:
        return None
    s_old, b_old = s_closes[-1 - bars], b_closes[-1 - bars]
    if not s_old or not b_old:
        return None
    return ((s_closes[-1] / s_old - 1) - (b_closes[-1] / b_old - 1)) * 100.0


def count_breadth(rows: list[tuple[float, float]]) -> tuple[int, int, float | None]:
    """rows: (close, previous_close) per stock on the target day. Returns
    (advancers, decliners, % advancing). Unchanged stocks are ignored.
    Breadth is None when fewer than MIN_BREADTH_STOCKS stocks moved."""
    advancing = sum(1 for close, prev in rows if close > prev)
    declining = sum(1 for close, prev in rows if close < prev)
    movers = advancing + declining
    if movers < MIN_BREADTH_STOCKS:
        return advancing, declining, None
    return advancing, declining, round(advancing / movers * 100.0, 1)


def compute_sector_rotation_rows(
    sector_closes: dict[str, list[tuple[int, float]]],
    benchmark: list[tuple[int, float]],
    stock_rows_by_sector: dict[str, dict[int, list[tuple[int, float]]]],
) -> list[dict]:
    """Pure core of the nightly computation.

    sector_closes          {spdr ticker: [(DateKey, close), ...] oldest first}
    benchmark              [(DateKey, index close), ...] oldest first
    stock_rows_by_sector   {spdr ticker: {asset_key: [(DateKey, close), ...] oldest first}}
    Returns one dict per sector ETF that has enough aligned history, for the
    latest date the ETF and the index share."""
    prepared = []
    for ticker in SECTOR_SPDR_TICKERS:
        dates, s_closes, b_closes = align_closes(sector_closes.get(ticker, []), benchmark)
        if len(dates) <= BARS_2D:
            continue
        as_of = dates[-1]

        movers = []
        for series in stock_rows_by_sector.get(ticker, {}).values():
            idx = next((i for i in range(len(series) - 1, -1, -1) if series[i][0] == as_of), None)
            if idx is not None and idx > 0 and series[idx - 1][1]:
                movers.append((series[idx][1], series[idx - 1][1]))
        advancing, declining, breadth = count_breadth(movers)
        prepared.append((ticker, as_of, s_closes, b_closes, advancing, declining, breadth))

    median = breadth_median(p[6] for p in prepared)
    out = []
    for ticker, as_of, s_closes, b_closes, advancing, declining, breadth in prepared:
        rel_2d = relative_return_pp(s_closes, b_closes, BARS_2D)
        rel_5d = relative_return_pp(s_closes, b_closes, BARS_5D)
        rel_20d = relative_return_pp(s_closes, b_closes, BARS_20D)
        reading = rotation_signal(rel_2d, rel_5d, breadth, median)
        out.append(
            {
                "ticker": ticker,
                "date_key": as_of,
                "rel_2d_pct": None if rel_2d is None else round(rel_2d, 3),
                "rel_5d_pct": None if rel_5d is None else round(rel_5d, 3),
                "rel_20d_pct": None if rel_20d is None else round(rel_20d, 3),
                "advancing": advancing,
                "declining": declining,
                "breadth_pct": breadth,
                "breadth_median_pct": median,
                "rotation_score": reading.score,
                "regime": reading.regime,
            }
        )
    return out


def compute_and_store_sector_rotation(conn, lookback_days: int = 90) -> list[dict]:
    """Reads the sector ETFs, the RSP benchmark and the S&P 1500 stocks from the
    database, computes the rotation rows and upserts them into
    FactSectorRotation. Returns the rows (for printing/reporting)."""
    from sqlalchemy import text

    cutoff = int((date.today() - timedelta(days=lookback_days)).strftime("%Y%m%d"))
    benchmark = [
        (dk, close)
        for dk, close in conn.execute(
            text(
                f'SELECT "DateKey", "{BENCHMARK_COLUMN}" FROM "MacroIndicators" '
                f'WHERE "{BENCHMARK_COLUMN}" IS NOT NULL AND "DateKey" >= :cut ORDER BY "DateKey"'
            ),
            {"cut": cutoff},
        )
    ]

    etf_keys = dict(
        conn.execute(
            text('SELECT "Ticker", "AssetKey" FROM "DimAsset" WHERE "Ticker" = ANY(:t)'), {"t": list(SECTOR_SPDR_TICKERS)}
        ).all()
    )
    sector_closes: dict[str, list[tuple[int, float]]] = {}
    for ticker, asset_key in etf_keys.items():
        sector_closes[ticker] = [
            (dk, close)
            for dk, close in conn.execute(
                text('SELECT "DateKey", "Close" FROM "FactDailyPrice" WHERE "AssetKey" = :a AND "DateKey" >= :cut ORDER BY "DateKey"'),
                {"a": asset_key, "cut": cutoff},
            )
        ]

    # Only the last ~2 weeks of closes are needed for a one-day breadth read.
    breadth_cut = int((date.today() - timedelta(days=14)).strftime("%Y%m%d"))
    stock_rows_by_sector: dict[str, dict[int, list[tuple[int, float]]]] = {}
    rows = conn.execute(
        text(
            """
            SELECT a."Sector", p."AssetKey", p."DateKey", p."Close"
            FROM "FactDailyPrice" p
            JOIN "DimAsset" a ON a."AssetKey" = p."AssetKey"
            WHERE a."Universe" = ANY(:universes) AND p."DateKey" >= :cut
            ORDER BY p."AssetKey", p."DateKey"
            """
        ),
        {"universes": list(BREADTH_UNIVERSES), "cut": breadth_cut},
    )
    for sector, asset_key, dk, close in rows:
        spdr = SECTOR_TO_SPDR.get(sector)
        if spdr is not None:
            stock_rows_by_sector.setdefault(spdr, {}).setdefault(asset_key, []).append((dk, close))

    results = compute_sector_rotation_rows(sector_closes, benchmark, stock_rows_by_sector)
    for r in results:
        conn.execute(
            text(
                """
                INSERT INTO "FactSectorRotation"
                    ("AssetKey", "DateKey", "Rel2DPct", "Rel5DPct", "Rel20DPct", "Advancing", "Declining",
                     "BreadthPct", "BreadthMedianPct", "RotationScore", "Regime", "ComputedAt")
                VALUES (:a, :dk, :r2, :r5, :r20, :adv, :dec, :b, :bm, :s, :regime, now())
                ON CONFLICT ("AssetKey", "DateKey") DO UPDATE SET
                    "Rel2DPct" = EXCLUDED."Rel2DPct", "Rel5DPct" = EXCLUDED."Rel5DPct",
                    "Rel20DPct" = EXCLUDED."Rel20DPct", "Advancing" = EXCLUDED."Advancing",
                    "Declining" = EXCLUDED."Declining", "BreadthPct" = EXCLUDED."BreadthPct",
                    "BreadthMedianPct" = EXCLUDED."BreadthMedianPct", "RotationScore" = EXCLUDED."RotationScore", "Regime" = EXCLUDED."Regime",
                    "ComputedAt" = now()
                """
            ),
            {
                "a": etf_keys[r["ticker"]],
                "dk": r["date_key"],
                "r2": r["rel_2d_pct"],
                "r5": r["rel_5d_pct"],
                "r20": r["rel_20d_pct"],
                "adv": r["advancing"],
                "dec": r["declining"],
                "b": r["breadth_pct"],
                "bm": r["breadth_median_pct"],
                "s": r["rotation_score"],
                "regime": r["regime"],
            },
        )
    return results
