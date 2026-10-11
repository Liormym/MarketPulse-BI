"""Thin read-only data-access layer for the webapp.

Reuses the same SQLAlchemy engine/settings as the pipeline (src/marketpulse)
so connection config stays in one place (.env / config.py).
"""
import sys
from pathlib import Path

SRC_PATH = str(Path(__file__).resolve().parents[1] / "src")
if SRC_PATH not in sys.path:
    sys.path.insert(0, SRC_PATH)

from sqlalchemy import text  # noqa: E402

from marketpulse.load.db import get_engine  # noqa: E402
from marketpulse.recent_news import safe_http_url  # noqa: E402
from marketpulse.scoring import RSI_COMPARE_BARS, SECTOR_RELATIVE_BARS_DEFAULT  # noqa: E402
from marketpulse.news_relevance import publisher_of, strip_publisher  # noqa: E402
from marketpulse.sector_flow import SECTOR_SPDR_TICKERS, SECTOR_TO_SPDR  # noqa: E402

# Historically, elevated yields/oil have put sustained pressure on equity
# valuations (higher discount rates, higher input/transport costs) - crossing
# either threshold surfaces the dashboard's "Macro Risk Alert" banner.
MACRO_RISK_10Y_YIELD_THRESHOLD = 4.25  # percent
MACRO_RISK_OIL_THRESHOLD = 85.0  # USD/barrel, WTI
SECTOR_RELATIVE_BARS = SECTOR_RELATIVE_BARS_DEFAULT  # trading bars for the sector-vs-S&P 500 comparison


def get_asset_key(ticker: str) -> int | None:
    with get_engine().connect() as conn:
        row = conn.execute(
            text('SELECT "AssetKey" FROM "DimAsset" WHERE "Ticker" = :ticker'), {"ticker": ticker}
        ).first()
    return row[0] if row else None


def get_tickers() -> list[dict]:
    """All assets from DimAsset, for populating the ticker picker."""
    query = text(
        """
        SELECT "Ticker", "CompanyName", "Sector"
        FROM "DimAsset"
        ORDER BY "Ticker"
        """
    )
    with get_engine().connect() as conn:
        rows = conn.execute(query).mappings().all()
    return [
        {"ticker": r["Ticker"], "name": r["CompanyName"], "sector": r["Sector"]}
        for r in rows
    ]


def get_price_history(ticker: str) -> dict | None:
    """Daily price, volume and technicals for one ticker, joined on DimDate.

    Returns None if the ticker isn't in DimAsset at all; returns an entry with
    empty series if the asset exists but has no FactDailyPrice rows yet.
    """
    asset_query = text(
        """
        SELECT "AssetKey", "Ticker", "CompanyName", "Sector"
        FROM "DimAsset"
        WHERE "Ticker" = :ticker
        """
    )
    history_query = text(
        """
        SELECT
            d."Date"                AS date,
            p."Close"               AS close,
            p."Open"                AS open,
            p."Volume"              AS volume,
            t."SMA20"               AS sma20,
            t."SMA50"               AS sma50,
            t."SMA150"              AS sma150,
            t."SMA200"              AS sma200,
            t."GapPct"              AS gap_pct,
            t."RSI14"               AS rsi14
        FROM "FactDailyPrice" p
        JOIN "DimDate" d ON d."DateKey" = p."DateKey"
        LEFT JOIN "FactStockTechnicals" t
            ON t."AssetKey" = p."AssetKey" AND t."DateKey" = p."DateKey"
        WHERE p."AssetKey" = :asset_key
        ORDER BY d."Date" ASC
        """
    )
    latest_technicals_query = text(
        """
        SELECT "ATR14", "ATR90Avg", "ATR90Std", "AvgVolume20D", "RSI14"
        FROM "FactStockTechnicals"
        WHERE "AssetKey" = :asset_key
        ORDER BY "DateKey" DESC
        LIMIT 1
        """
    )

    def _round(value, ndigits):
        return round(value, ndigits) if value is not None else None

    with get_engine().connect() as conn:
        asset = conn.execute(asset_query, {"ticker": ticker}).mappings().first()
        if asset is None:
            return None

        rows = conn.execute(history_query, {"asset_key": asset["AssetKey"]}).mappings().all()
        latest_technicals = conn.execute(
            latest_technicals_query, {"asset_key": asset["AssetKey"]}
        ).mappings().first()

    sector_spdr = SECTOR_TO_SPDR.get(asset["Sector"])
    prices = [_round(r["close"], 2) for r in rows]

    # RSI RSI_COMPARE_BARS bars before the latest one (for the score's "RSI is
    # recovering" check). Bars are consecutive trading days of this asset.
    rsi_series = [r["rsi14"] for r in rows]
    rsi_prev = (
        rsi_series[-1 - RSI_COMPARE_BARS] if len(rsi_series) > RSI_COMPARE_BARS else None
    )

    latest_price = prices[-1] if prices else None
    daily_change_pct = None
    if len(prices) >= 2 and prices[-2]:
        daily_change_pct = round((prices[-1] - prices[-2]) / prices[-2] * 100, 2)

    return {
        "ticker": asset["Ticker"],
        "name": asset["CompanyName"],
        "sector": asset["Sector"],
        "sector_spdr": sector_spdr,
        "latest_price": latest_price,
        "daily_change_pct": daily_change_pct,
        "dates": [r["date"].isoformat() for r in rows],
        "prices": prices,
        "opens": [_round(r["open"], 2) for r in rows],
        "volumes": [r["volume"] for r in rows],
        "sma20": [_round(r["sma20"], 2) for r in rows],
        "sma50": [_round(r["sma50"], 2) for r in rows],
        "sma150": [_round(r["sma150"], 2) for r in rows],
        "sma200": [_round(r["sma200"], 2) for r in rows],
        "gap_pct": [_round(r["gap_pct"], 2) for r in rows],
        "atr14": _round(latest_technicals["ATR14"], 2) if latest_technicals else None,
        "atr_90d_avg": _round(latest_technicals["ATR90Avg"], 2) if latest_technicals else None,
        "atr_90d_std": _round(latest_technicals["ATR90Std"], 2) if latest_technicals else None,
        "avg_volume_20d": _round(latest_technicals["AvgVolume20D"], 0) if latest_technicals else None,
        "rsi14": _round(latest_technicals["RSI14"], 1) if latest_technicals else None,
        "rsi14_prev": _round(rsi_prev, 1),
    }


def build_score_kwargs(data: dict, sector_flow: dict | None, enrichment: dict | None) -> dict:
    """Assembles the exact kwargs compute_investment_score() needs, given
    the raw ingredients (a get_price_history() payload, a
    get_sector_flows() row, and an enrich_stock() result).

    This is the SINGLE SOURCE for that assembly - webapp/app.py's
    /api/stock/<ticker> route and scripts/compute_investment_scores.py both
    call it rather than each building the kwargs list by hand. That's a
    direct fix for a real bug: the batch script used to hand-roll its own
    enrichment lookup (cached-only, no yfinance fetch) instead of calling
    enrich_stock() like the live route does, so a ticker whose enrichment
    cache was empty or stale silently scored without the insider-selling
    penalty in the batch path while the live route (which fetches fresh on a
    cache miss) applied it - two different numbers for the same ticker,
    depending only on which screen you looked at. Routing both entry points
    through one assembly function makes that class of drift structurally
    impossible, not just coincidentally avoided.
    """
    return dict(
        closes=data["prices"],
        sma20_series=data["sma20"],
        sma50_series=data["sma50"],
        sma200_series=data["sma200"],
        rsi14=data["rsi14"],
        rsi14_prev=data["rsi14_prev"],
        atr14=data["atr14"],
        atr_90d_avg=data["atr_90d_avg"],
        atr_90d_std=data["atr_90d_std"],
        sector_flow_status=sector_flow["flow_status"] if sector_flow else None,
        sector_rel_20d_pct=sector_flow["rel_20d_pct"] if sector_flow else None,
        short_percent_of_float=enrichment["short_percent_of_float"] if enrichment else None,
        insider_net=enrichment["insider_net"] if enrichment else None,
    )


def get_macro_snapshot() -> dict | None:
    """Latest known value for each macro indicator, independently - yields
    (FRED) and oil (yfinance) don't always publish for the same trading day,
    so picking a single "latest row" can null out a field that actually has
    a more recent value a day or two back. `as_of` is the MAX across every
    series' own latest date (not just yields/oil) - Bitcoin trades 24/7 and
    can be a day fresher than the FRED-sourced yields, so the dashboard's
    freshness badge should reflect whichever series is actually newest, not
    understate it by only checking two of the eight tracked series."""
    query = text(
        """
        SELECT
            (SELECT MAX(d."Date") FROM "MacroIndicators" m JOIN "DimDate" d ON d."DateKey" = m."DateKey"
                WHERE m."TenYearYield" IS NOT NULL) AS ten_year_as_of,
            (SELECT m."TenYearYield" FROM "MacroIndicators" m JOIN "DimDate" d ON d."DateKey" = m."DateKey"
                WHERE m."TenYearYield" IS NOT NULL ORDER BY d."Date" DESC LIMIT 1) AS ten_year_yield,
            (SELECT m."TwoYearYield" FROM "MacroIndicators" m JOIN "DimDate" d ON d."DateKey" = m."DateKey"
                WHERE m."TwoYearYield" IS NOT NULL ORDER BY d."Date" DESC LIMIT 1) AS two_year_yield,
            (SELECT MAX(d."Date") FROM "MacroIndicators" m JOIN "DimDate" d ON d."DateKey" = m."DateKey"
                WHERE m."CrudeOilPrice" IS NOT NULL) AS oil_as_of,
            (SELECT MAX(d."Date") FROM "MacroIndicators" m JOIN "DimDate" d ON d."DateKey" = m."DateKey"
                WHERE m."BitcoinPrice" IS NOT NULL) AS bitcoin_as_of,
            (SELECT MAX(d."Date") FROM "MacroIndicators" m JOIN "DimDate" d ON d."DateKey" = m."DateKey"
                WHERE m."KospiIndex" IS NOT NULL) AS kospi_as_of,
            (SELECT MAX(d."Date") FROM "MacroIndicators" m JOIN "DimDate" d ON d."DateKey" = m."DateKey"
                WHERE m."SP500Index" IS NOT NULL) AS sp500_as_of,
            (SELECT MAX(d."Date") FROM "MacroIndicators" m JOIN "DimDate" d ON d."DateKey" = m."DateKey"
                WHERE m."NasdaqIndex" IS NOT NULL) AS nasdaq_as_of,
            (SELECT MAX(d."Date") FROM "MacroIndicators" m JOIN "DimDate" d ON d."DateKey" = m."DateKey"
                WHERE m."RSPPrice" IS NOT NULL) AS rsp_as_of,
            (SELECT m."CrudeOilPrice" FROM "MacroIndicators" m JOIN "DimDate" d ON d."DateKey" = m."DateKey"
                WHERE m."CrudeOilPrice" IS NOT NULL ORDER BY d."Date" DESC LIMIT 1) AS crude_oil_price,
            (SELECT m."BitcoinPrice" FROM "MacroIndicators" m JOIN "DimDate" d ON d."DateKey" = m."DateKey"
                WHERE m."BitcoinPrice" IS NOT NULL ORDER BY d."Date" DESC LIMIT 1) AS bitcoin_price,
            (SELECT m."KospiIndex" FROM "MacroIndicators" m JOIN "DimDate" d ON d."DateKey" = m."DateKey"
                WHERE m."KospiIndex" IS NOT NULL ORDER BY d."Date" DESC LIMIT 1) AS kospi_index,
            (SELECT m."TenYearYieldChangePct" FROM "MacroIndicators" m JOIN "DimDate" d ON d."DateKey" = m."DateKey"
                WHERE m."TenYearYield" IS NOT NULL ORDER BY d."Date" DESC LIMIT 1) AS ten_year_change_pct,
            (SELECT m."TwoYearYieldChangePct" FROM "MacroIndicators" m JOIN "DimDate" d ON d."DateKey" = m."DateKey"
                WHERE m."TwoYearYield" IS NOT NULL ORDER BY d."Date" DESC LIMIT 1) AS two_year_change_pct,
            (SELECT m."CrudeOilChangePct" FROM "MacroIndicators" m JOIN "DimDate" d ON d."DateKey" = m."DateKey"
                WHERE m."CrudeOilPrice" IS NOT NULL ORDER BY d."Date" DESC LIMIT 1) AS crude_oil_change_pct,
            (SELECT m."BitcoinChangePct" FROM "MacroIndicators" m JOIN "DimDate" d ON d."DateKey" = m."DateKey"
                WHERE m."BitcoinPrice" IS NOT NULL ORDER BY d."Date" DESC LIMIT 1) AS bitcoin_change_pct,
            (SELECT m."KospiChangePct" FROM "MacroIndicators" m JOIN "DimDate" d ON d."DateKey" = m."DateKey"
                WHERE m."KospiIndex" IS NOT NULL ORDER BY d."Date" DESC LIMIT 1) AS kospi_change_pct,
            (SELECT m."SP500Index" FROM "MacroIndicators" m JOIN "DimDate" d ON d."DateKey" = m."DateKey"
                WHERE m."SP500Index" IS NOT NULL ORDER BY d."Date" DESC LIMIT 1) AS sp500_index,
            (SELECT m."SP500ChangePct" FROM "MacroIndicators" m JOIN "DimDate" d ON d."DateKey" = m."DateKey"
                WHERE m."SP500Index" IS NOT NULL ORDER BY d."Date" DESC LIMIT 1) AS sp500_change_pct,
            (SELECT m."NasdaqIndex" FROM "MacroIndicators" m JOIN "DimDate" d ON d."DateKey" = m."DateKey"
                WHERE m."NasdaqIndex" IS NOT NULL ORDER BY d."Date" DESC LIMIT 1) AS nasdaq_index,
            (SELECT m."NasdaqChangePct" FROM "MacroIndicators" m JOIN "DimDate" d ON d."DateKey" = m."DateKey"
                WHERE m."NasdaqIndex" IS NOT NULL ORDER BY d."Date" DESC LIMIT 1) AS nasdaq_change_pct,
            (SELECT m."RSPPrice" FROM "MacroIndicators" m JOIN "DimDate" d ON d."DateKey" = m."DateKey"
                WHERE m."RSPPrice" IS NOT NULL ORDER BY d."Date" DESC LIMIT 1) AS rsp_price,
            (SELECT m."RSPChangePct" FROM "MacroIndicators" m JOIN "DimDate" d ON d."DateKey" = m."DateKey"
                WHERE m."RSPPrice" IS NOT NULL ORDER BY d."Date" DESC LIMIT 1) AS rsp_change_pct
        """
    )
    with get_engine().connect() as conn:
        row = conn.execute(query).mappings().first()
    as_of_candidates = [
        row[key]
        for key in ("ten_year_as_of", "oil_as_of", "bitcoin_as_of", "kospi_as_of", "sp500_as_of", "nasdaq_as_of", "rsp_as_of")
    ] if row is not None else []
    as_of_candidates = [d for d in as_of_candidates if d is not None]
    if not as_of_candidates:
        return None
    as_of = max(as_of_candidates)
    macro_risk_alert = (
        row["ten_year_yield"] is not None and row["ten_year_yield"] > MACRO_RISK_10Y_YIELD_THRESHOLD
    ) or (row["crude_oil_price"] is not None and row["crude_oil_price"] > MACRO_RISK_OIL_THRESHOLD)
    return {
        "as_of": as_of.isoformat(),
        "ten_year_yield": row["ten_year_yield"],
        "ten_year_change_pct": row["ten_year_change_pct"],
        "two_year_yield": row["two_year_yield"],
        "two_year_change_pct": row["two_year_change_pct"],
        "crude_oil_price": row["crude_oil_price"],
        "crude_oil_change_pct": row["crude_oil_change_pct"],
        "bitcoin_price": row["bitcoin_price"],
        "bitcoin_change_pct": row["bitcoin_change_pct"],
        "kospi_index": row["kospi_index"],
        "kospi_change_pct": row["kospi_change_pct"],
        "sp500_index": row["sp500_index"],
        "sp500_change_pct": row["sp500_change_pct"],
        "nasdaq_index": row["nasdaq_index"],
        "nasdaq_change_pct": row["nasdaq_change_pct"],
        "rsp_price": row["rsp_price"],
        "rsp_change_pct": row["rsp_change_pct"],
        "macro_risk_alert": macro_risk_alert,
    }


def get_sector_flows() -> list[dict]:
    """Latest Accumulation/Distribution/Neutral status for all 11 sector
    SPDRs, plus each ETF's own 1-day price % change - computed live with the
    same windowed LAG() query get_top_movers() uses (not stored: it's only
    11 tickers, so recomputing it live is cheap, and it keeps one proven
    pattern for "daily price % change" instead of a second stored copy that
    could drift out of sync with FactDailyPrice)."""
    query = text(
        """
        SELECT DISTINCT ON (a."Ticker")
            a."Ticker", a."CompanyName", d."Date",
            fsv."Volume", fsv."AvgVolume20D", fsv."VolumeRatio", fsv."FlowStatus"
        FROM "FactSectorVolume" fsv
        JOIN "DimAsset" a ON a."AssetKey" = fsv."AssetKey"
        JOIN "DimDate" d ON d."DateKey" = fsv."DateKey"
        WHERE a."Ticker" = ANY(:tickers)
        ORDER BY a."Ticker", d."Date" DESC
        """
    )
    change_query = text(
        """
        WITH recent AS (
            SELECT p."AssetKey", d."Date", p."Close"
            FROM "FactDailyPrice" p
            JOIN "DimDate" d ON d."DateKey" = p."DateKey"
            JOIN "DimAsset" a ON a."AssetKey" = p."AssetKey"
            WHERE a."Ticker" = ANY(:tickers) AND d."Date" >= CURRENT_DATE - INTERVAL '10 days'
        ),
        ranked AS (
            SELECT
                "AssetKey", "Date", "Close",
                LAG("Close") OVER (PARTITION BY "AssetKey" ORDER BY "Date") AS prev_close,
                ROW_NUMBER() OVER (PARTITION BY "AssetKey" ORDER BY "Date" DESC) AS rn
            FROM recent
        )
        SELECT a."Ticker", r."Close", r."prev_close"
        FROM ranked r
        JOIN "DimAsset" a ON a."AssetKey" = r."AssetKey"
        WHERE r.rn = 1
        """
    )
    relative_query = text(
        """
        SELECT a."Ticker", d."Date", p."Close"
        FROM "FactDailyPrice" p
        JOIN "DimDate" d ON d."DateKey" = p."DateKey"
        JOIN "DimAsset" a ON a."AssetKey" = p."AssetKey"
        WHERE a."Ticker" = ANY(:tickers) AND d."Date" >= CURRENT_DATE - INTERVAL '60 days'
        ORDER BY a."Ticker", d."Date"
        """
    )
    spx_query = text(
        """
        SELECT m."SP500Index"
        FROM "MacroIndicators" m JOIN "DimDate" d ON d."DateKey" = m."DateKey"
        WHERE m."SP500Index" IS NOT NULL AND d."Date" >= CURRENT_DATE - INTERVAL '60 days'
        ORDER BY d."Date"
        """
    )
    with get_engine().connect() as conn:
        rows = conn.execute(query, {"tickers": SECTOR_SPDR_TICKERS}).mappings().all()
        change_rows = conn.execute(change_query, {"tickers": SECTOR_SPDR_TICKERS}).mappings().all()
        closes_rows = conn.execute(relative_query, {"tickers": SECTOR_SPDR_TICKERS}).all()
        spx = [r[0] for r in conn.execute(spx_query)]

    # 20-trading-bar return of each sector ETF minus the S&P 500's, in percentage points.
    closes_by_ticker: dict[str, list[float]] = {}
    for ticker, _, close in closes_rows:
        closes_by_ticker.setdefault(ticker, []).append(close)
    spx_ret = (spx[-1] / spx[-1 - SECTOR_RELATIVE_BARS] - 1) * 100 if len(spx) > SECTOR_RELATIVE_BARS else None
    rel_by_ticker = {}
    for ticker, closes in closes_by_ticker.items():
        if spx_ret is not None and len(closes) > SECTOR_RELATIVE_BARS and closes[-1 - SECTOR_RELATIVE_BARS]:
            rel_by_ticker[ticker] = round((closes[-1] / closes[-1 - SECTOR_RELATIVE_BARS] - 1) * 100 - spx_ret, 2)

    change_by_ticker = {
        r["Ticker"]: round((r["Close"] - r["prev_close"]) / r["prev_close"] * 100, 2)
        for r in change_rows
        if r["prev_close"]
    }

    return [
        {
            "ticker": r["Ticker"],
            "name": r["CompanyName"],
            "as_of": r["Date"].isoformat(),
            "volume_ratio": round(r["VolumeRatio"], 2) if r["VolumeRatio"] is not None else None,
            "flow_status": r["FlowStatus"],
            "daily_change_pct": change_by_ticker.get(r["Ticker"]),
            "rel_20d_pct": rel_by_ticker.get(r["Ticker"]),
        }
        for r in rows
    ]


def get_top_movers(limit: int = 25) -> list[dict]:
    """Largest daily % movers (by absolute value) across the whole watchlist,
    using each ticker's latest two trading days.

    Filters FactDailyPrice down to a recent date window BEFORE computing the
    LAG() window function - the table now holds 4.5M+ rows going back to the
    1980s (period=max backfill), and windowing the full history on every
    dashboard load was taking ~1.8s vs. ~10-70ms for the other dashboard
    endpoints. 10 calendar days comfortably covers weekends/holidays while
    still being a tiny slice of the table.
    """
    query = text(
        """
        WITH recent AS (
            SELECT p."AssetKey", d."Date", p."Close"
            FROM "FactDailyPrice" p
            JOIN "DimDate" d ON d."DateKey" = p."DateKey"
            WHERE d."Date" >= CURRENT_DATE - INTERVAL '10 days'
        ),
        ranked AS (
            SELECT
                "AssetKey", "Date", "Close",
                LAG("Close") OVER (PARTITION BY "AssetKey" ORDER BY "Date") AS prev_close,
                ROW_NUMBER() OVER (PARTITION BY "AssetKey" ORDER BY "Date" DESC) AS rn
            FROM recent
        )
        SELECT a."Ticker", a."CompanyName", r."Close", r."prev_close"
        FROM ranked r
        JOIN "DimAsset" a ON a."AssetKey" = r."AssetKey"
        WHERE r.rn = 1 AND r."prev_close" IS NOT NULL AND r."prev_close" != 0
        """
    )
    with get_engine().connect() as conn:
        rows = conn.execute(query).mappings().all()

    movers = []
    for r in rows:
        change_pct = (r["Close"] - r["prev_close"]) / r["prev_close"] * 100
        movers.append(
            {
                "ticker": r["Ticker"],
                "name": r["CompanyName"],
                "price": round(r["Close"], 2),
                "change_pct": round(change_pct, 2),
            }
        )
    movers.sort(key=lambda m: abs(m["change_pct"]), reverse=True)
    return movers[:limit]


def get_top_strong_buys(limit: int = 5) -> list[dict]:
    """Top-N tickers by cached Investment Score. Refreshed by
    scripts/compute_investment_scores.py - recomputing the full score for
    every watchlist ticker live on each dashboard load (price+technicals+
    sector-flow joins, per ticker) would be far too slow, the same
    reasoning get_top_movers's date-windowing already documents, just more
    so since a score is a heavier computation than a single price delta."""
    query = text(
        """
        SELECT a."Ticker", a."CompanyName", fis."Score", fis."ComputedAt"
        FROM "FactInvestmentScore" fis
        JOIN "DimAsset" a ON a."AssetKey" = fis."AssetKey"
        ORDER BY fis."Score" DESC, a."Ticker" ASC  -- exact score first, ticker only breaks a true tie
        LIMIT :limit
        """
    )
    with get_engine().connect() as conn:
        rows = conn.execute(query, {"limit": limit}).mappings().all()
    return [
        {
            "ticker": r["Ticker"],
            "name": r["CompanyName"],
            "score": round(r["Score"], 1),  # display value; ranking uses the exact score
            "score_exact": round(r["Score"], 4),
            "as_of": r["ComputedAt"].isoformat(),
        }
        for r in rows
    ]


def enrich_stock(ticker: str) -> dict | None:
    """Short interest + insider transactions, fetched on-demand and cached
    (see enrichment.py - these are point-in-time snapshots, not daily
    history, so they're not part of the batch technicals computation)."""
    from enrichment import get_or_fetch_enrichment

    asset_key = get_asset_key(ticker)
    if asset_key is None:
        return None
    with get_engine().begin() as conn:
        return get_or_fetch_enrichment(conn, asset_key, ticker)


def get_recent_news(ticker: str) -> list[dict]:
    """The stored latest headlines that explicitly name this ticker, newest
    first. Plain news only: no sentiment sign, score or colour is exposed
    (news is context, not a score input). Only http(s) URLs are linked
    (re-checked here as defence in depth)."""
    query = text(
        """
        SELECT n."Title", n."SourceUrl", n."PublishedAt"
        FROM "StockRecentNews" rn
        JOIN "DimAsset" a ON a."AssetKey" = rn."AssetKey"
        JOIN "NewsArticles" n ON n."ArticleID" = rn."ArticleID"
        WHERE a."Ticker" = :ticker
        ORDER BY rn."Rank"
        """
    )
    with get_engine().connect() as conn:
        rows = conn.execute(query, {"ticker": ticker}).mappings().all()
    return [
        {
            # The " - Publisher" Google News appends is shown as the source instead.
            "title": strip_publisher(r["Title"]),
            "source": publisher_of(r["Title"]),
            "url": r["SourceUrl"] if safe_http_url(r["SourceUrl"]) else None,
            "published_at": r["PublishedAt"].isoformat(),
        }
        for r in rows
    ]


def get_market_brief() -> dict | None:
    """Latest Daily Macro Brief, or None before the first one is generated."""
    query = text(
        """
        SELECT d."Date", b."Brief", b."GeneratedAt"
        FROM "DailyMarketBrief" b JOIN "DimDate" d ON d."DateKey" = b."DateKey"
        ORDER BY d."Date" DESC LIMIT 1
        """
    )
    with get_engine().connect() as conn:
        row = conn.execute(query).mappings().first()
    if row is None:
        return None
    return {
        "as_of": row["Date"].isoformat(),
        "brief": row["Brief"],
        "generated_at": row["GeneratedAt"].isoformat(),
    }


def _utc(dt):
    return dt.isoformat() if dt is not None else None


def get_freshness() -> dict:
    """Real timestamps (UTC ISO) for every global data source, plus the
    schedule, so the UI can say exactly how old each thing is. A source with
    no timestamp yet is reported as None, never guessed."""
    from datetime import datetime, timezone

    from marketpulse.schedule import (
        describe_schedule,
        is_overdue,
        last_scheduled_run,
        next_scheduled_run,
        us_market_open,
    )

    with get_engine().connect() as conn:
        rows = conn.execute(text('SELECT "Source", "RefreshedAt" FROM "DataFreshness"')).all()
    stamps = {source: ts for source, ts in rows}
    now = datetime.now(timezone.utc)
    return {
        "server_now": now.isoformat(),
        "sources": {
            s: {"refreshed_at": _utc(stamps.get(s)), "overdue": is_overdue(stamps.get(s), now)}
            for s in ("eod_update", "prices", "news", "macro", "sector_flows", "scores", "brief")
        },
        "schedule": {
            "description": describe_schedule(),
            "last_scheduled_run": last_scheduled_run(now).isoformat(),
            "next_scheduled_run": next_scheduled_run(now).isoformat(),
        },
        "market_open": us_market_open(now),
    }


def get_stock_freshness(asset_key: int, latest_bar_date) -> dict:
    """Per-stock freshness for the deep-dive page."""
    from datetime import datetime, timezone

    from marketpulse.schedule import is_overdue, us_market_open

    with get_engine().connect() as conn:
        row = conn.execute(
            text(
                """
                SELECT "PricesFetchedAt", "NewsFetchedAt", "TechnicalsComputedAt", "LastRefreshKind"
                FROM "StockDataFreshness" WHERE "AssetKey" = :a
                """
            ),
            {"a": asset_key},
        ).mappings().first()
        drivers = conn.execute(
            text('SELECT MAX("ComputedAt") FROM "StockRecentNews" WHERE "AssetKey" = :a'), {"a": asset_key}
        ).scalar()
    now = datetime.now(timezone.utc)
    prices = row["PricesFetchedAt"] if row else None
    return {
        "server_now": now.isoformat(),
        "latest_bar_date": latest_bar_date.isoformat() if latest_bar_date else None,
        "prices_fetched_at": _utc(prices),
        "news_fetched_at": _utc(row["NewsFetchedAt"]) if row else None,
        "technicals_computed_at": _utc(row["TechnicalsComputedAt"]) if row else None,
        "news_panel_computed_at": _utc(drivers),
        "last_refresh_kind": row["LastRefreshKind"] if row else None,
        "prices_overdue": is_overdue(prices, now),
        "market_open": us_market_open(now),
    }
