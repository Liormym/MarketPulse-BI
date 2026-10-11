#!/usr/bin/env python3
"""Weekly forward-return evaluation of the Investment Score (v2).

Question answered: do stocks with a higher score go on to beat stocks with a
lower score? Measured as the cross-sectional rank correlation (Spearman IC)
between each day's score and the return over the NEXT 5 / 20 / 60 trading
days, from the real history in FactInvestmentScoreHistory (ScoreVersion = 2
only: v1 sentiment-based rows are not comparable and are ignored).

Also reported per horizon:
  * component attribution: the same IC for each pillar's points on its own
    (Trend, Momentum/RSI, Sector, Risk), to see which part, if any, carries signal
  * signal buckets: mean excess return of Strong (70+), Mixed (45-69), Weak (<45)
  * top-quintile minus bottom-quintile spread
  * Newey-West t-statistic for the mean IC (daily ICs over a multi-day horizon
    overlap, so a plain t-stat would overstate significance)

HONESTY RULES, baked in:
  * A horizon with no score date old enough prints "INSUFFICIENT DATA" with
    exactly how many more trading days are needed. It never prints a number.
  * With fewer than MIN_DATES_FOR_STATS usable dates the figures are labelled
    PRELIMINARY and no t-statistic is shown: a handful of overlapping dates is
    an anecdote, not evidence.
  * Excess return = stock return minus the cross-sectional median that day.

Exit code is 0 whenever the evaluation ran (even if data was insufficient);
non-zero only if the database could not be read.

Usage: PYTHONPATH=src .venv/bin/python scripts/evaluate_score_ic.py [--json PATH]
Run it weekly (e.g. Monday morning); the history table does the accumulating.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import spearmanr
from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

HORIZONS = (5, 20, 60)  # trading days
MIN_DATES_FOR_STATS = 10  # usable score dates before figures stop being "preliminary"
MIN_STOCKS_PER_DATE = 30  # cross-section too small to rank below this
COMPONENTS = {
    "Trend & Moving Averages": "TrendPoints",
    "Momentum & RSI-14": "MomentumPoints",
    "Sector Momentum": "SectorPoints",
    "Short & Risk Profile": "RiskPoints",
}


def load_panels(engine) -> tuple[pd.DataFrame, pd.DataFrame]:
    scores = pd.read_sql(
        text(
            """
            SELECT h."AssetKey" AS asset, d."Date" AS date, h."Score" AS score,
                   h."TrendPoints", h."MomentumPoints", h."SectorPoints", h."RiskPoints"
            FROM "FactInvestmentScoreHistory" h JOIN "DimDate" d ON d."DateKey" = h."DateKey"
            WHERE h."ScoreVersion" = 2
            """
        ),
        engine,
    )
    if scores.empty:
        return scores, pd.DataFrame()
    scores["date"] = pd.to_datetime(scores["date"])
    closes = pd.read_sql(
        text(
            """
            SELECT p."AssetKey" AS asset, d."Date" AS date, p."Close" AS close
            FROM "FactDailyPrice" p JOIN "DimDate" d ON d."DateKey" = p."DateKey"
            WHERE d."Date" >= :since AND p."AssetKey" = ANY(:assets)
            """
        ),
        engine,
        params={"since": scores["date"].min().date(), "assets": [int(a) for a in scores["asset"].unique()]},
    )
    closes["date"] = pd.to_datetime(closes["date"])
    return scores, closes.pivot(index="date", columns="asset", values="close").sort_index()


def market_calendar(prices: pd.DataFrame) -> list[pd.Timestamp]:
    """Trading days = dates on which most of the scored universe has a bar
    (drops weekend bars that only a few 24/7 assets have)."""
    need = 0.5 * prices.shape[1]
    return [d for d, n in prices.notna().sum(axis=1).items() if n >= need]


def newey_west_t(x: np.ndarray, lag: int) -> float | None:
    n = len(x)
    if n < 3:
        return None
    lag = max(0, min(lag, n - 2))
    xc = x - x.mean()
    var = float(np.dot(xc, xc) / n)
    for k in range(1, lag + 1):
        w = 1 - k / (lag + 1)
        var += 2 * w * float(np.dot(xc[k:], xc[:-k]) / n)
    if var <= 0:
        return None
    return float(x.mean() / math.sqrt(var / n))


def _ic(a: pd.Series, b: pd.Series) -> float | None:
    if len(a) < MIN_STOCKS_PER_DATE or a.nunique() < 3 or b.nunique() < 3:
        return None
    r = spearmanr(a, b)[0]
    return None if r is None or np.isnan(r) else float(r)


def evaluate_horizon(scores: pd.DataFrame, prices: pd.DataFrame, calendar: list, h: int) -> dict:
    pos = {d: i for i, d in enumerate(calendar)}
    last_score_date = scores["date"].max()
    latest_bar = calendar[-1]
    out: dict = {"horizon_days": h, "latest_score_date": last_score_date.date().isoformat()}

    rows = []
    for d, g in scores.groupby("date"):
        i = pos.get(d)
        if i is None or i + h >= len(calendar):
            continue
        end = calendar[i + h]
        ret = (prices.loc[end] / prices.loc[d] - 1).rename("ret")
        m = g.set_index("asset").join(ret).dropna(subset=["ret", "score"])
        if len(m) < MIN_STOCKS_PER_DATE:
            continue
        m["excess"] = m["ret"] - m["ret"].median()
        rows.append((d, m))

    out["usable_dates"] = len(rows)
    if not rows:
        first = scores["date"].min()
        i = pos.get(first)
        have = (len(calendar) - 1 - i) if i is not None else 0
        out["status"] = "INSUFFICIENT DATA"
        out["detail"] = (
            f"needs {h} trading days of prices after a score date; the earliest score "
            f"({first.date()}) has {have} so far (latest price bar {latest_bar.date()}). "
            f"About {max(h - have, 0)} more trading day(s) needed for the first {h}d reading."
        )
        return out

    ics, comp = [], {name: [] for name in COMPONENTS}
    quintile, buckets = [], {"Strong (70+)": [], "Mixed (45-69)": [], "Weak (<45)": []}
    for d, m in rows:
        ic = _ic(m["score"], m["excess"])
        if ic is not None:
            ics.append((d, ic))
        for name, col in COMPONENTS.items():
            if col in m and m[col].notna().all():
                c = _ic(m[col], m["excess"])
                if c is not None:
                    comp[name].append(c)
        hi, lo = m["score"].quantile(0.8), m["score"].quantile(0.2)
        if hi > lo:
            quintile.append(m.loc[m["score"] >= hi, "excess"].mean() - m.loc[m["score"] <= lo, "excess"].mean())
        for label, mask in (
            ("Strong (70+)", m["score"] >= 70),
            ("Mixed (45-69)", (m["score"] >= 45) & (m["score"] < 70)),
            ("Weak (<45)", m["score"] < 45),
        ):
            if mask.sum() >= 5:
                buckets[label].append(m.loc[mask, "excess"].mean())

    series = np.array([v for _, v in sorted(ics)])
    preliminary = len(series) < MIN_DATES_FOR_STATS
    out["status"] = "PRELIMINARY" if preliminary else "OK"
    out["dates_used"] = len(series)
    out["avg_stocks"] = round(float(np.mean([len(m) for _, m in rows])), 0)
    out["mean_ic"] = round(float(series.mean()), 4) if len(series) else None
    out["pct_dates_positive"] = round(float((series > 0).mean()), 3) if len(series) else None
    out["ic_t_newey_west"] = None if preliminary else (
        None if (t := newey_west_t(series, h - 1)) is None else round(t, 2)
    )
    out["top_minus_bottom_quintile_pct"] = round(float(np.mean(quintile)) * 100, 2) if quintile else None
    out["buckets_mean_excess_pct"] = {k: round(float(np.mean(v)) * 100, 2) for k, v in buckets.items() if v}
    out["component_mean_ic"] = {k: round(float(np.mean(v)), 4) for k, v in comp.items() if v}
    if preliminary:
        out["detail"] = (
            f"only {len(series)} usable score date(s) (need {MIN_DATES_FOR_STATS}) and they overlap: "
            "treat as an anecdote, no significance test shown"
        )
    return out


def evaluate(scores: pd.DataFrame, prices: pd.DataFrame, horizons=HORIZONS) -> list[dict]:
    # Only dates with a real cross-section count (a few stray rows on a weekend
    # bar are not a score date).
    per_date = scores.groupby("date")["asset"].transform("count")
    scores = scores[per_date >= MIN_STOCKS_PER_DATE]
    calendar = market_calendar(prices)
    return [evaluate_horizon(scores, prices, calendar, h) for h in horizons]


def format_report(results: list[dict], n_dates: int, first: str, last: str) -> str:
    lines = [
        "Investment Score v2: forward-return evaluation",
        f"Score history: {n_dates} date(s), {first} to {last} (v2 rows only)",
        "IC = cross-sectional Spearman rank correlation between score and excess forward return",
        "",
    ]
    for r in results:
        head = f"--- {r['horizon_days']}-trading-day horizon: {r['status']}"
        lines.append(head)
        if r["status"] == "INSUFFICIENT DATA":
            lines += [f"    {r['detail']}", ""]
            continue
        lines.append(f"    dates used {r['dates_used']}, avg stocks {r['avg_stocks']:.0f}")
        t = r["ic_t_newey_west"]
        lines.append(
            f"    mean IC {r['mean_ic']:+.4f}  | dates with IC > 0: {r['pct_dates_positive']:.0%}"
            + (f"  | Newey-West t {t:+.2f}" if t is not None else "")
        )
        if r.get("top_minus_bottom_quintile_pct") is not None:
            lines.append(f"    top quintile minus bottom quintile: {r['top_minus_bottom_quintile_pct']:+.2f}%")
        if r["buckets_mean_excess_pct"]:
            lines.append("    mean excess return by signal: " + ", ".join(f"{k} {v:+.2f}%" for k, v in r["buckets_mean_excess_pct"].items()))
        if r["component_mean_ic"]:
            lines.append("    component IC: " + ", ".join(f"{k} {v:+.3f}" for k, v in r["component_mean_ic"].items()))
        if r.get("detail"):
            lines.append(f"    NOTE: {r['detail']}")
        lines.append("")
    lines.append("Rule of thumb: |t| below ~2, or a mean IC within +/-0.02, is indistinguishable from noise.")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", help="also write the results to this JSON file")
    args = ap.parse_args()

    from marketpulse.load.db import get_engine

    try:
        scores, prices = load_panels(get_engine())
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: could not read the database: {exc}", file=sys.stderr)
        return 2

    if scores.empty:
        print("No score-v2 history yet (run scripts/compute_investment_scores.py). Nothing to evaluate.")
        return 0

    full = scores[scores.groupby("date")["asset"].transform("count") >= MIN_STOCKS_PER_DATE]
    if full.empty:
        print("No score-v2 date with a full cross-section yet. Nothing to evaluate.")
        return 0
    results = evaluate(scores, prices)
    print(format_report(results, full["date"].nunique(), full["date"].min().date().isoformat(), full["date"].max().date().isoformat()))
    if args.json:
        Path(args.json).write_text(json.dumps(results, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
