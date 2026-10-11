"""Daily Macro Brief: a 1-2 sentence, rule-based market summary.

No LLM and no external API: every sentence is assembled from thresholds on
numbers already in the database (Treasury yields, S&P 500 / NASDAQ / RSP,
oil, the 11 sector SPDRs, and breadth across the watchlist's GICS-sector
stocks), so the same inputs always give the same text and every claim can be
audited in the stored `Facts` JSON.

Layout:
  gather_facts(conn)   SQL -> plain dict of facts (the only DB-reading part)
  build_brief(facts)   pure function, facts -> text (unit-tested)
  generate_and_store   glue used by scripts/generate_market_brief.py

Like scoring.py, the thresholds below are documented heuristics, not
backtested constants.
"""
from __future__ import annotations

import json
import logging
from datetime import date, datetime, timezone

from sqlalchemy import text
from sqlalchemy.engine import Connection

from .sector_flow import SECTOR_SPDR_TICKERS, SECTOR_TO_SPDR

log = logging.getLogger(__name__)

SPDR_TO_SECTOR = {v: k for k, v in SECTOR_TO_SPDR.items()}

INDEX_MOVE_PCT = 0.5  # |S&P 500 daily change| at or above this is a rally / sell-off
INDEX_STRONG_PCT = 1.5  # ... and at or above this, a strong one
YIELD_MOVE_BPS = 3.0  # 10Y moves smaller than this read as "steady"
YIELD_MILESTONES = (5.0, 4.75, 4.5, 4.25, 4.0, 3.75, 3.5)  # levels worth naming
OIL_MOVE_PCT = 2.0
SECTOR_MOVE_PCT = 1.0  # a sector must move this much to be called out
BREADTH_STRONG = 65.0  # % of stocks up
BREADTH_WEAK = 35.0
RSP_LAG_PCT = 0.3  # equal-weight lagging the cap-weighted index by this much = narrow


# --------------------------------------------------------------------------
# facts
# --------------------------------------------------------------------------
def _two_latest(conn: Connection, column: str, change_column: str | None = None):
    """Latest non-null (date, value[, change]) of a MacroIndicators column."""
    cols = f'm."{column}"' + (f', m."{change_column}"' if change_column else "")
    row = conn.execute(
        text(
            f"""
            SELECT d."Date", {cols}
            FROM "MacroIndicators" m JOIN "DimDate" d ON d."DateKey" = m."DateKey"
            WHERE m."{column}" IS NOT NULL
            ORDER BY d."Date" DESC LIMIT 1
            """
        )
    ).first()
    return row


def _latest_change_pairs(conn: Connection, tickers: list[str]) -> dict[str, tuple[date, float]]:
    """ticker -> (latest bar date, 1-day % change) from the last two bars."""
    rows = conn.execute(
        text(
            """
            WITH ranked AS (
                SELECT a."Ticker", d."Date", p."Close",
                       LAG(p."Close") OVER (PARTITION BY p."AssetKey" ORDER BY d."Date") AS prev_close,
                       ROW_NUMBER() OVER (PARTITION BY p."AssetKey" ORDER BY d."Date" DESC) AS rn
                FROM "FactDailyPrice" p
                JOIN "DimDate" d ON d."DateKey" = p."DateKey"
                JOIN "DimAsset" a ON a."AssetKey" = p."AssetKey"
                WHERE a."Ticker" = ANY(:tickers) AND d."Date" >= CURRENT_DATE - INTERVAL '14 days'
            )
            SELECT "Ticker", "Date", "Close", prev_close FROM ranked WHERE rn = 1
            """
        ),
        {"tickers": tickers},
    ).all()
    return {
        t: (d, (c - pc) / pc * 100) for t, d, c, pc in rows if pc and pc > 0 and c is not None
    }


def gather_facts(conn: Connection) -> dict | None:
    """Returns None when there is no macro data at all (nothing to summarise)."""
    ten = _two_latest(conn, "TenYearYield", "TenYearYieldChangePct")
    oil = _two_latest(conn, "CrudeOilPrice", "CrudeOilChangePct")
    spx = _two_latest(conn, "SP500Index", "SP500ChangePct")
    ndx = _two_latest(conn, "NasdaqIndex", "NasdaqChangePct")
    rsp = _two_latest(conn, "RSPPrice", "RSPChangePct")
    if not any((ten, oil, spx)):
        return None

    sector_changes = _latest_change_pairs(conn, SECTOR_SPDR_TICKERS)
    sectors = {
        t: {"name": SPDR_TO_SECTOR[t], "change_pct": round(chg, 2)}
        for t, (_, chg) in sector_changes.items()
    }

    stock_tickers = [
        r[0]
        for r in conn.execute(
            text('SELECT "Ticker" FROM "DimAsset" WHERE "Sector" = ANY(:s)'),
            {"s": list(SECTOR_TO_SPDR)},
        )
        if r[0] not in SECTOR_SPDR_TICKERS
    ]
    stock_changes = _latest_change_pairs(conn, stock_tickers)
    breadth = None
    if len(stock_changes) >= 20:  # too few bars would make the percentage noise
        up = sum(1 for _, chg in stock_changes.values() if chg > 0)
        breadth = {"pct_up": round(up / len(stock_changes) * 100, 1), "n": len(stock_changes)}

    # The brief belongs to the market date it describes: the newest bar/reading used.
    dates = [x[0] for x in (ten, oil, spx, ndx, rsp) if x] + [d for d, _ in sector_changes.values()]
    market_date = max(dates)

    return {
        "market_date": market_date.isoformat(),
        "ten_year": {"yield": ten[1], "change_pct": ten[2]} if ten else None,
        "oil": {"price": oil[1], "change_pct": oil[2]} if oil else None,
        "sp500_change_pct": spx[2] if spx else None,
        "nasdaq_change_pct": ndx[2] if ndx else None,
        "rsp_change_pct": rsp[2] if rsp else None,
        "sectors": sectors,
        "breadth": breadth,
    }


# --------------------------------------------------------------------------
# text
# --------------------------------------------------------------------------
def _yield_bps(y: dict) -> float | None:
    """1-day move in basis points, from the stored relative % change."""
    if y is None or y.get("yield") is None or y.get("change_pct") is None:
        return None
    prev = y["yield"] / (1 + y["change_pct"] / 100)
    return (y["yield"] - prev) * 100


def _milestone(level: float, bps: float) -> float | None:
    """A round yield level worth naming, if today's move touched or crossed one."""
    prev = level - bps / 100
    for m in YIELD_MILESTONES:
        if min(level, prev) <= m <= max(level, prev):
            return m
    return None


def _market_clause(facts: dict) -> str | None:
    chg = facts.get("sp500_change_pct")
    if chg is None:
        return None
    ndx = facts.get("nasdaq_change_pct")
    if chg >= INDEX_STRONG_PCT:
        return f"Stocks rally hard, with the S&P 500 up {chg:.1f}%"
    if chg >= INDEX_MOVE_PCT:
        return f"Stocks advance, with the S&P 500 up {chg:.1f}%"
    if chg <= -INDEX_STRONG_PCT:
        return f"Stocks sell off sharply, with the S&P 500 down {abs(chg):.1f}%"
    if chg <= -INDEX_MOVE_PCT:
        return f"Stocks slip, with the S&P 500 down {abs(chg):.1f}%"
    if ndx is not None and abs(ndx - chg) >= 0.5:
        lead = "NASDAQ outperforms" if ndx > chg else "NASDAQ lags"
        return f"Indices are little changed, but {lead} the S&P 500"
    return "Indices are little changed"


def _rates_clause(facts: dict) -> str | None:
    y = facts.get("ten_year")
    bps = _yield_bps(y)
    if bps is None:
        return None
    level = y["yield"]
    if abs(bps) < YIELD_MOVE_BPS:
        return f"the 10Y Treasury yield holds near {level:.2f}%"
    ms = _milestone(level, bps)
    if bps > 0:
        tail = f"above {ms:g}%" if ms is not None and level >= ms else f"to {level:.2f}%"
        return f"the 10Y Treasury yield climbs {tail}"
    tail = f"below {ms:g}%" if ms is not None and level < ms else f"to {level:.2f}%"
    return f"the 10Y Treasury yield eases {tail}"


def _verb(name: str, singular: str, plural: str) -> str:
    """Sector names like "Utilities" or "Financials" take a plural verb."""
    return plural if name.endswith("s") else singular


def _sector_sentence(facts: dict) -> str | None:
    sectors = facts.get("sectors") or {}
    if len(sectors) < 6:
        return None
    ranked = sorted(sectors.values(), key=lambda s: s["change_pct"])
    worst, best = ranked[0], ranked[-1]
    parts = []
    if best["change_pct"] >= SECTOR_MOVE_PCT:
        verb = _verb(best["name"], "surges", "surge") if best["change_pct"] >= 2.5 else _verb(best["name"], "leads", "lead")
        parts.append(f"{best['name']} {verb} ({best['change_pct']:+.1f}%)")
    if worst["change_pct"] <= -SECTOR_MOVE_PCT:
        verb = (
            _verb(worst["name"], "tumbles", "tumble")
            if worst["change_pct"] <= -2.5
            else _verb(worst["name"], "faces pressure", "face pressure")
        )
        parts.append(f"{worst['name']} {verb} ({worst['change_pct']:+.1f}%)")
    return " while ".join(parts) if parts else None


def _oil_clause(facts: dict) -> str | None:
    o = facts.get("oil")
    if not o or o.get("change_pct") is None:
        return None
    if o["change_pct"] >= OIL_MOVE_PCT:
        return f"crude oil jumps {o['change_pct']:.1f}% to ${o['price']:.0f}"
    if o["change_pct"] <= -OIL_MOVE_PCT:
        return f"crude oil slides {abs(o['change_pct']):.1f}% to ${o['price']:.0f}"
    return None


def _breadth_clause(facts: dict) -> str | None:
    b = facts.get("breadth")
    spx, rsp = facts.get("sp500_change_pct"), facts.get("rsp_change_pct")
    if spx is not None and rsp is not None and spx - rsp >= RSP_LAG_PCT and spx > 0:
        return "gains are narrow, led by mega-caps (equal-weight RSP lags)"
    if b:
        if b["pct_up"] >= BREADTH_STRONG:
            return f"breadth is strong, with {b['pct_up']:.0f}% of stocks higher"
        if b["pct_up"] <= BREADTH_WEAK:
            return f"breadth is weak, with only {b['pct_up']:.0f}% of stocks higher"
    return None


def build_brief(facts: dict) -> str:
    """1-2 sentences. Sentence 1: market direction + the rates backdrop.
    Sentence 2: what stands out underneath (sectors, oil, breadth)."""
    market, rates = _market_clause(facts), _rates_clause(facts)
    if market and rates:
        first = f"{market} as {rates}."
    elif market:
        first = f"{market}."
    elif rates:
        first = f"{rates[0].upper()}{rates[1:]}."
    else:
        first = None

    second_parts = [p for p in (_sector_sentence(facts), _oil_clause(facts), _breadth_clause(facts)) if p]
    second = None
    if second_parts:
        # Sector call-outs are already a full clause; oil/breadth join with ';'.
        joined = "; ".join(second_parts)
        second = f"{joined[0].upper()}{joined[1:]}."

    sentences = [s for s in (first, second) if s]
    return " ".join(sentences) if sentences else "Markets are quiet: no major moves across rates, oil or sectors."


# --------------------------------------------------------------------------
# persistence
# --------------------------------------------------------------------------
def generate_and_store(conn: Connection) -> tuple[str, dict] | None:
    facts = gather_facts(conn)
    if facts is None:
        log.warning("no macro data available; skipping market brief")
        return None
    brief = build_brief(facts)
    market_date = date.fromisoformat(facts["market_date"])
    conn.execute(
        text(
            """
            INSERT INTO "DailyMarketBrief" ("DateKey", "Brief", "Facts", "GeneratedAt")
            VALUES (:date_key, :brief, CAST(:facts AS JSONB), :now)
            ON CONFLICT ("DateKey") DO UPDATE
                SET "Brief" = EXCLUDED."Brief", "Facts" = EXCLUDED."Facts",
                    "GeneratedAt" = EXCLUDED."GeneratedAt"
            """
        ),
        {
            "date_key": int(market_date.strftime("%Y%m%d")),
            "brief": brief,
            "facts": json.dumps(facts),
            "now": datetime.now(timezone.utc),
        },
    )
    return brief, facts
