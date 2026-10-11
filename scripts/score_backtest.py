#!/usr/bin/env python3
"""Point-in-time backtest of the Investment Score v2 (pure quantitative).

FactInvestmentScore keeps one row per stock, and the score history only starts
in October 2026, so this reconstructs what the REAL scoring function
(marketpulse.scoring.compute_investment_score, the exact v2 formula: three
pillars, RSI-14 regime, risk, CEO/CFO insider modifier) would have returned at
each month-end, using only data available on that day, then measures how the
score relates to the following 1/3/6/12 months. Entry is the NEXT trading day's
close, so there is no look-ahead on the signal bar.

What is reconstructed faithfully (all trailing-only calculations):
  trend (SMA20/50/200), RSI-14 and its 5-bar change, ATR z-score, sector money
  flow, sector 20-day return vs the S&P 500.
What cannot be, and how it is handled (each is then NEUTRAL for every stock, so
it cannot create a spurious spread):
  short interest: only a current snapshot exists -> neutral.
  CEO/CFO insider modifier: Form 4 data starts 2024-09-30 -> applied only from
    2024-12-31 (a full 90-day window); before that it is 0.
Known biases (they flatter the score, so a weak result is NOT explained by them):
  survivorship (today's watchlist/S&P 500 membership is used for all history).

Usage: PYTHONPATH=src .venv/bin/python scripts/score_backtest.py [--out reports]
"""
import argparse
import json
import math
import pickle
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import spearmanr
from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from marketpulse.insider import net_insider_activity  # noqa: E402
from marketpulse.load.db import get_engine  # noqa: E402
from marketpulse.scoring import SECTOR_RELATIVE_BARS_DEFAULT, compute_investment_score  # noqa: E402
from marketpulse.sector_flow import SECTOR_SPDR_TICKERS, SECTOR_TO_SPDR  # noqa: E402

HORIZONS = {"1M": 21, "3M": 63, "6M": 126, "12M": 252}
NW_LAG = {"1M": 0, "3M": 2, "6M": 5, "12M": 11}  # horizons overlap across month-end dates
LOAD_FROM = "2003-06-01"
FIRST_REBALANCE = "2005-01-31"
INSIDER_COVERED_FROM = np.datetime64("2024-12-31")
MIN_STOCKS_PER_DATE = 30
WINDOWS = {"2005 to today": "2005-01-01", "2015 to today": "2015-01-01", "Dec 2024 to today (insider data)": "2024-12-01"}
PILLARS = ["trend_pts", "momentum_pts", "sector_pts", "risk_pts", "insider_pts"]


def nz1(v):
    return None if (v is None or (isinstance(v, float) and math.isnan(v))) else float(v)


def nz(arr):
    return [nz1(v) for v in arr]


def load_sp500(cache: Path) -> pd.Series:
    """S&P 500 index closes; MacroIndicators only holds ~3 years, so the full
    history is fetched once from Yahoo and cached next to the reports."""
    if cache.exists():
        s = pd.read_csv(cache, index_col=0, parse_dates=True).iloc[:, 0]
        return s
    import yfinance as yf

    s = yf.Ticker("^GSPC").history(period="max", interval="1d")["Close"]
    s.index = pd.to_datetime(s.index).tz_localize(None).normalize()
    cache.parent.mkdir(parents=True, exist_ok=True)
    s.to_csv(cache, header=["close"])
    return s


def load(conn):
    prices = pd.read_sql(
        text(
            """
            SELECT a."Ticker" AS ticker, a."Sector" AS sector, d."Date" AS date, p."Close" AS close,
                   t."SMA20" AS sma20, t."SMA50" AS sma50, t."SMA200" AS sma200, t."RSI14" AS rsi14,
                   t."ATR14" AS atr14, t."ATR90Avg" AS atr90avg, t."ATR90Std" AS atr90std
            FROM "FactDailyPrice" p
            JOIN "DimAsset" a ON a."AssetKey" = p."AssetKey"
            JOIN "DimDate" d ON d."DateKey" = p."DateKey"
            LEFT JOIN "FactStockTechnicals" t ON t."AssetKey" = p."AssetKey" AND t."DateKey" = p."DateKey"
            WHERE a."Sector" NOT IN ('Index', 'ETF', 'Crypto') AND a."Ticker" NOT LIKE '%%.%%' AND d."Date" >= :start
            ORDER BY a."Ticker", d."Date"
            """
        ),
        conn,
        params={"start": LOAD_FROM},
    )
    flows = pd.read_sql(
        text(
            """
            SELECT a."Ticker" AS spdr, d."Date" AS date, f."FlowStatus" AS status
            FROM "FactSectorVolume" f JOIN "DimAsset" a ON a."AssetKey" = f."AssetKey"
            JOIN "DimDate" d ON d."DateKey" = f."DateKey"
            WHERE a."Ticker" = ANY(:t) AND d."Date" >= :start ORDER BY a."Ticker", d."Date"
            """
        ),
        conn,
        params={"t": list(SECTOR_SPDR_TICKERS), "start": LOAD_FROM},
    )
    etf = pd.read_sql(
        text(
            """
            SELECT a."Ticker" AS spdr, d."Date" AS date, p."Close" AS close
            FROM "FactDailyPrice" p JOIN "DimAsset" a ON a."AssetKey" = p."AssetKey"
            JOIN "DimDate" d ON d."DateKey" = p."DateKey"
            WHERE a."Ticker" = ANY(:t) AND d."Date" >= :start ORDER BY a."Ticker", d."Date"
            """
        ),
        conn,
        params={"t": list(SECTOR_SPDR_TICKERS), "start": LOAD_FROM},
    )
    insiders = pd.read_sql(
        text(
            """
            SELECT a."Ticker" AS ticker, i."TransactionDate" AS date, i."Value" AS value, i."Shares" AS shares,
                   i."IsExecutiveSale" AS is_executive_sale, i."IsExecutiveBuy" AS is_executive_buy
            FROM "InsiderTransactions" i JOIN "DimAsset" a ON a."AssetKey" = i."AssetKey"
            WHERE i."IsExecutiveSale" OR i."IsExecutiveBuy" ORDER BY a."Ticker", i."TransactionDate"
            """
        ),
        conn,
    )
    return prices, flows, etf, insiders


def build_panel(prices, flows, etf, insiders, sp500):
    flow_dates, flow_status = {}, {}
    for spdr, g in flows.groupby("spdr"):
        flow_dates[spdr] = pd.to_datetime(g["date"]).values.astype("datetime64[D]")
        flow_status[spdr] = g["status"].to_numpy()

    etf_dates, etf_close = {}, {}
    for spdr, g in etf.groupby("spdr"):
        etf_dates[spdr] = pd.to_datetime(g["date"]).values.astype("datetime64[D]")
        etf_close[spdr] = g["close"].to_numpy(float)
    sp_dates = sp500.index.values.astype("datetime64[D]")
    sp_close = sp500.to_numpy(float)

    def rel20(spdr, day):
        """Sector ETF 20-bar return minus S&P 500 20-bar return, in percentage points."""
        if spdr not in etf_dates:
            return None
        k = int(np.searchsorted(etf_dates[spdr], day, side="right")) - 1
        j = int(np.searchsorted(sp_dates, day, side="right")) - 1
        if k < SECTOR_RELATIVE_BARS_DEFAULT or j < SECTOR_RELATIVE_BARS_DEFAULT:
            return None
        e = (etf_close[spdr][k] / etf_close[spdr][k - SECTOR_RELATIVE_BARS_DEFAULT] - 1) * 100
        s = (sp_close[j] / sp_close[j - SECTOR_RELATIVE_BARS_DEFAULT] - 1) * 100
        return float(e - s)

    insider_rows = {
        t: [
            {
                "transaction_date": r.date,
                "value": None if pd.isna(r.value) else float(r.value),
                "shares": None if pd.isna(r.shares) else int(r.shares),
                "is_executive_sale": bool(r.is_executive_sale),
                "is_executive_buy": bool(r.is_executive_buy),
            }
            for r in g.itertuples()
        ]
        for t, g in insiders.groupby("ticker")
    }

    last_date = pd.to_datetime(prices["date"]).max()
    month_ends = pd.date_range(FIRST_REBALANCE, last_date, freq="ME").values.astype("datetime64[D]")
    rows = []

    for ticker, g in prices.groupby("ticker", sort=False):
        n = len(g)
        if n < 260:
            continue
        dates = pd.to_datetime(g["date"]).values.astype("datetime64[D]")
        close = g["close"].to_numpy(float)
        sma20, sma50, sma200, rsi = (g[c].to_numpy(float) for c in ("sma20", "sma50", "sma200", "rsi14"))
        atr14, a_avg, a_std = (g[c].to_numpy(float) for c in ("atr14", "atr90avg", "atr90std"))
        sector = g["sector"].iloc[0]
        spdr = SECTOR_TO_SPDR.get(sector)
        txns = insider_rows.get(ticker, [])

        for me in month_ends:
            i = int(np.searchsorted(dates, me, side="right")) - 1
            if i < 199 or (me - dates[i]) > np.timedelta64(7, "D"):
                continue
            e = i + 1
            if e >= n:
                continue

            status = None
            if spdr in flow_dates:
                k = int(np.searchsorted(flow_dates[spdr], dates[i], side="right")) - 1
                if k >= 0:
                    status = flow_status[spdr][k]

            covered = dates[i] >= INSIDER_COVERED_FROM
            net = net_insider_activity(txns, today=pd.Timestamp(dates[i]).date())["net"] if covered else None

            w = slice(i - 199, i + 1)
            r = compute_investment_score(
                closes=close[w].tolist(),
                sma20_series=nz(sma20[w]),
                sma50_series=nz(sma50[w]),
                sma200_series=nz(sma200[w]),
                rsi14=nz1(rsi[i]),
                rsi14_prev=nz1(rsi[i - 5]) if i >= 5 else None,
                atr14=nz1(atr14[i]),
                atr_90d_avg=nz1(a_avg[i]),
                atr_90d_std=nz1(a_std[i]),
                sector_flow_status=status,
                sector_rel_20d_pct=rel20(spdr, dates[i]),
                short_percent_of_float=None,  # not available point-in-time: neutral for everyone
                insider_net=net,
            )
            if r.score is None:
                continue

            row = {
                "ticker": ticker, "sector": sector, "date": dates[i], "score": r.score,
                "trend_pts": r.trend_points, "momentum_pts": r.momentum_points, "sector_pts": r.sector_points,
                "risk_pts": r.risk_points, "insider_pts": r.insider_points, "rsi14": nz1(rsi[i]) or np.nan,
                "insider_net": net or "", "insider_covered": bool(covered),
                "entry_date": dates[e], "ret_to_latest": close[n - 1] / close[e] - 1, "latest_date": dates[n - 1],
            }
            for name, h in HORIZONS.items():
                x = e + h
                ok = x < n and (dates[x] - dates[e]) <= np.timedelta64(int(h * 1.5 + 10), "D")
                row[f"ret_{name}"] = close[x] / close[e] - 1 if ok else np.nan
            rows.append(row)

    panel = pd.DataFrame(rows)
    for c in ("date", "entry_date", "latest_date"):
        panel[c] = pd.to_datetime(panel[c])
    for name in HORIZONS:
        col = f"ret_{name}"
        panel[f"ex_{name}"] = panel[col] - panel.groupby("date")[col].transform("mean")
    panel["rsi_regime"] = pd.cut(
        panel["rsi14"], [-1, 30, 45, 65, 70, 101], labels=["<30 oversold", "30-45", "45-65 healthy", "65-70", ">70 overbought"]
    )
    return panel


def nw_tstat(x, lag):
    x = np.asarray(x, float)
    x = x[~np.isnan(x)]
    n = len(x)
    if n < 5:
        return np.nan
    lag = min(lag, n - 2)
    xc = x - x.mean()
    var = float(np.dot(xc, xc) / n)
    for k in range(1, lag + 1):
        var += 2 * (1 - k / (lag + 1)) * float(np.dot(xc[k:], xc[:-k]) / n)
    return float(x.mean() / math.sqrt(var / n)) if var > 0 else np.nan


def per_date_ic(df, col, ret):
    out = []
    for _, g in df.groupby("date"):
        g = g.dropna(subset=[col, ret])
        if len(g) >= MIN_STOCKS_PER_DATE and g[col].nunique() > 2:
            out.append(spearmanr(g[col], g[ret])[0])
    return np.array(out)


def ic_table(panel, col="score"):
    rows = []
    for wname, start in WINDOWS.items():
        sub = panel[panel["date"] >= start]
        for h in HORIZONS:
            ics = per_date_ic(sub, col, f"ex_{h}")
            if len(ics):
                rows.append({"window": wname, "horizon": h, "dates": len(ics), "mean_ic": ics.mean(),
                             "t_nw": nw_tstat(ics, NW_LAG[h]), "pct_positive": (ics > 0).mean()})
    return pd.DataFrame(rows)


def band_of(s):
    return "Strong 70+" if s >= 70 else ("Mixed 45-69" if s >= 45 else "Weak <45")


def band_table(panel):
    p = panel.assign(band=panel["score"].map(band_of))
    rows = []
    for wname, start in WINDOWS.items():
        sub = p[p["date"] >= start]
        for h in HORIZONS:
            ex = f"ex_{h}"
            per = sub.dropna(subset=[ex]).groupby(["date", "band"])[ex].agg(["mean", "count"]).reset_index()
            per = per[per["count"] >= 5]
            wide = per.pivot(index="date", columns="band", values="mean")
            if "Strong 70+" not in wide or "Weak <45" not in wide:
                continue
            spread = (wide["Strong 70+"] - wide["Weak <45"]).dropna()
            rows.append({
                "window": wname, "horizon": h, "dates": len(spread),
                **{f"{b} mean excess %": 100 * wide[b].mean() for b in ("Strong 70+", "Mixed 45-69", "Weak <45") if b in wide},
                "Strong minus Weak %": 100 * spread.mean(), "t_nw": nw_tstat(spread, NW_LAG[h]),
                "pct_dates_strong_wins": (spread > 0).mean(),
            })
    return pd.DataFrame(rows)


def component_table(panel):
    rows = []
    for col in PILLARS:
        sub = panel if col != "insider_pts" else panel[panel["insider_covered"]]
        for h in ("1M", "3M"):
            ics = per_date_ic(sub, col, f"ex_{h}")
            if len(ics):
                rows.append({"component": col, "horizon": h, "dates": len(ics), "mean_ic": ics.mean(), "t_nw": nw_tstat(ics, NW_LAG[h])})
    return pd.DataFrame(rows)


def rsi_table(panel):
    rows = []
    for h in ("1M", "3M"):
        g = panel.dropna(subset=["rsi14", f"ex_{h}"])
        per = g.groupby(["date", "rsi_regime"], observed=True)[f"ex_{h}"].agg(["mean", "count"]).reset_index()
        per = per[per["count"] >= 5]
        for regime, s in per.groupby("rsi_regime", observed=True):
            rows.append({"horizon": h, "rsi_regime": str(regime), "dates": len(s), "mean_excess_%": 100 * s["mean"].mean(),
                         "avg_stocks_per_date": s["count"].mean()})
    return pd.DataFrame(rows)


def strong_by_rsi(panel):
    s = panel[panel["score"] >= 70]
    out = {"strong_obs": int(len(s)), "strong_with_rsi_over_70": int((s["rsi14"] > 70).sum()),
           "max_score_when_rsi_over_70": float(panel.loc[panel["rsi14"] > 70, "score"].max()),
           "share_of_rsi_over_70_that_are_strong": float((panel.loc[panel["rsi14"] > 70, "score"] >= 70).mean())}
    for h in ("1M", "3M"):
        for label, m in (("rsi_le_70", s["rsi14"] <= 70), ("rsi_gt_70", s["rsi14"] > 70)):
            v = s.loc[m, f"ex_{h}"].dropna()
            out[f"strong_{label}_{h}_mean_excess_%"] = float(100 * v.mean()) if len(v) else None
            out[f"strong_{label}_{h}_n"] = int(len(v))
    return out


def insider_effect(panel):
    c = panel[panel["insider_covered"]]
    rows = []
    for h in ("1M", "3M"):
        for net in ("selling", "buying", ""):
            v = c.loc[c["insider_net"] == net, f"ex_{h}"].dropna()
            rows.append({"horizon": h, "insider_net": net or "none", "obs": len(v), "mean_excess_%": 100 * v.mean() if len(v) else None})
    return pd.DataFrame(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="reports")
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    sp500 = load_sp500(out / "_sp500_history.csv")
    with get_engine().connect() as conn:
        prices, flows, etf, insiders = load(conn)
    panel = build_panel(prices, flows, etf, insiders, sp500)
    panel.to_csv(out / "score_v2_panel.csv", index=False)

    res = {
        "meta": {"stocks": int(panel["ticker"].nunique()), "observations": int(len(panel)),
                 "dates": int(panel["date"].nunique()), "first": str(panel["date"].min().date()),
                 "last": str(panel["date"].max().date()), "mean_score": float(panel["score"].mean()),
                 "median_score": float(panel["score"].median())},
        "ic": ic_table(panel), "bands": band_table(panel), "components": component_table(panel),
        "rsi": rsi_table(panel), "strong_by_rsi": strong_by_rsi(panel), "insider": insider_effect(panel),
    }
    with open(out / "_results_v2.pkl", "wb") as f:
        pickle.dump(res, f)
    json_safe = {k: (v.to_dict("records") if isinstance(v, pd.DataFrame) else v) for k, v in res.items()}
    (out / "score_v2_backtest.json").write_text(json.dumps(json_safe, indent=2, default=str))

    pd.set_option("display.width", 200, "display.max_columns", 20, "display.float_format", lambda v: f"{v:,.3f}")
    print(json.dumps(res["meta"], indent=2))
    for k in ("ic", "bands", "components", "rsi", "insider"):
        print(f"\n== {k}\n{res[k].to_string(index=False)}")
    print("\n== strong_by_rsi\n" + json.dumps(res["strong_by_rsi"], indent=2))


if __name__ == "__main__":
    main()
