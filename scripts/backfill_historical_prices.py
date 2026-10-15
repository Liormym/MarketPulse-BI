"""Historical price backfill: downloads daily OHLCV history via yfinance and idempotently
upserts it into FactDailyPrice (and any missing DimDate rows).

Used for the one-time load of a newly added universe (e.g. the S&P 1500 + TASE
expansion) and for top-ups. Built for BULK runs against a provider that throttles:

  * batches (default 100 tickers): fetch -> upsert -> COMMIT, so progress is never lost
  * resumable: --only-missing skips tickers that already have enough history
  * polite: a shared rate limiter, a gap between batches, and an automatic global
    pause (30 s, doubling to 10 min) whenever Yahoo answers "too many requests"
  * progress with ETA, and failed tickers are written to a file for a retry run
  * agorot-quoted TASE instruments are divided by 100 at load (DimAsset.PriceDivisor)

Safe to re-run: (AssetKey, DateKey) upserts never create duplicates.

Usage:
  PYTHONPATH=src .venv/bin/python scripts/backfill_historical_prices.py --period 10y --only-missing \\
        --universe sp400,sp600,tase --min-interval 0.4 --workers 4
  PYTHONPATH=src .venv/bin/python scripts/backfill_historical_prices.py --period max      # everything
  ... --prune-short-history 200   # afterwards: drop newly added tickers with < 200 trading days
"""
import argparse
import sys
import time
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from sqlalchemy import text  # noqa: E402
from sqlalchemy.engine import Connection  # noqa: E402

from marketpulse.config import settings  # noqa: E402
from marketpulse.extract import prices as prices_module  # noqa: E402
from marketpulse.extract.prices import fetch_prices  # noqa: E402
from marketpulse.extract.ratelimit import OUTBOUND_LIMITER  # noqa: E402
from marketpulse.load.db import get_engine  # noqa: E402
from marketpulse.load.upsert import get_asset_key_map, get_price_divisors, upsert_daily_prices  # noqa: E402

MIN_BARS_FOR_SCORE = 200


def ensure_dim_dates(conn: Connection, trade_dates) -> int:
    """Upserts a DimDate row for every distinct date in the fetched history (10 years back
    can reach earlier than the rolling window seed_dimensions.py normally covers)."""
    dates = sorted(set(trade_dates))
    for d in dates:
        conn.execute(
            text(
                """
                INSERT INTO "DimDate" ("DateKey", "Date", "IsTradingDay")
                VALUES (:date_key, :date, TRUE)
                ON CONFLICT ("DateKey") DO NOTHING
                """
            ),
            {"date_key": int(d.strftime("%Y%m%d")), "date": d},
        )
    return len(dates)


def select_tickers(conn: Connection, universes: list[str] | None, only_missing: bool) -> list[str]:
    rows = conn.execute(
        text(
            """
            SELECT a."Ticker", COALESCE(c.n, 0) AS bars
            FROM "DimAsset" a
            LEFT JOIN (SELECT "AssetKey", count(*) AS n FROM "FactDailyPrice" GROUP BY 1) c USING ("AssetKey")
            WHERE (:all_universes OR a."Universe" = ANY(:universes))
            ORDER BY a."Ticker"
            """
        ),
        {"all_universes": not universes, "universes": universes or []},
    ).all()
    return [t for t, bars in rows if not only_missing or bars < MIN_BARS_FOR_SCORE]


def prune_short_history(engine, min_bars: int, universes: list[str]) -> list[str]:
    """Removes newly added instruments that turned out to have fewer than `min_bars` trading
    days (recent listings): from DimAsset, their price rows, and config/watchlist.yaml, so
    the next seed does not re-add them. Only the given universes are ever touched."""
    with engine.begin() as conn:
        short = [
            r[0]
            for r in conn.execute(
                text(
                    """
                    SELECT a."Ticker" FROM "DimAsset" a
                    LEFT JOIN (SELECT "AssetKey", count(*) AS n FROM "FactDailyPrice" GROUP BY 1) c USING ("AssetKey")
                    WHERE a."Universe" = ANY(:u) AND COALESCE(c.n, 0) < :m
                    """
                ),
                {"u": universes, "m": min_bars},
            )
        ]
        for t in short:
            key = conn.execute(text('SELECT "AssetKey" FROM "DimAsset" WHERE "Ticker" = :t'), {"t": t}).scalar()
            for table in ("FactDailyPrice", "FactStockTechnicals", "FactInvestmentScore", "FactInvestmentScoreHistory",
                          "StockRecentNews", "StockDataFreshness", "StockEnrichmentCache", "InsiderTransactions",
                          "NewsArticles", "FactSentiment"):
                conn.execute(text(f'DELETE FROM "{table}" WHERE "AssetKey" = :k'), {"k": key})
            conn.execute(text('DELETE FROM "DimAsset" WHERE "AssetKey" = :k'), {"k": key})
    if short:
        path = settings.watchlist_abs_path
        data = yaml.safe_load(path.read_text())
        keep = [a for a in data["assets"] if a["ticker"] not in set(short)]
        header = "".join(line for line in path.read_text().splitlines(True) if line.startswith("#"))

        class _D(yaml.Dumper):
            def increase_indent(self, flow=False, indentless=False):
                return super().increase_indent(flow, False)

        with path.open("w") as f:
            f.write(header)
            yaml.dump({"assets": keep}, f, Dumper=_D, default_flow_style=False, sort_keys=False, allow_unicode=True)
    return short


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--days", type=int, default=730, help="Calendar days of history (default 730; ignored with --period)")
    p.add_argument("--period", default=None, help="yfinance period, e.g. 10y or max (overrides --days)")
    p.add_argument("--universe", default=None, help="comma list: sp500,sp400,sp600,tase,personal,etf,index,crypto")
    p.add_argument("--only-missing", action="store_true", help="skip tickers that already have >= 200 bars (resume)")
    p.add_argument("--batch-size", type=int, default=100)
    p.add_argument("--batch-sleep", type=float, default=5.0, help="seconds between batches")
    p.add_argument("--min-interval", type=float, default=None, help="seconds between outbound requests (default: config)")
    p.add_argument("--workers", type=int, default=None, help="parallel fetch workers (default: config)")
    p.add_argument("--limit", type=int, default=None, help="only the first N tickers (trial run)")
    p.add_argument("--failed-file", default="reports/backfill_failed.txt")
    p.add_argument("--prune-short-history", type=int, default=None, metavar="BARS",
                   help="after loading, remove tickers (in --universe) with fewer than BARS trading days")
    args = p.parse_args()

    if args.min_interval is not None:
        OUTBOUND_LIMITER._min_interval = args.min_interval  # noqa: SLF001 - bulk-run override of the shared limiter
    if args.workers:
        settings.price_max_workers = args.workers
    universes = [u.strip() for u in args.universe.split(",")] if args.universe else None
    period = args.period

    engine = get_engine()
    with engine.connect() as conn:
        tickers = select_tickers(conn, universes, args.only_missing)[: args.limit]
        asset_keys = get_asset_key_map(conn)
        divisors = get_price_divisors(conn)
    label = period or f"{args.days}d"
    print(f"Backfilling {label} for {len(tickers)} tickers in batches of {args.batch_size} "
          f"(interval {OUTBOUND_LIMITER._min_interval}s, {settings.price_max_workers} workers)", flush=True)  # noqa: SLF001

    started = time.monotonic()
    total_rows = 0
    failed_all: list[str] = []
    for b in range(0, len(tickers), args.batch_size):
        batch = tickers[b : b + args.batch_size]
        records, failed = fetch_prices(batch, lookback_days=args.days, period=period, divisors=divisors)
        with engine.begin() as conn:
            ensure_dim_dates(conn, (r.trade_date for r in records))
            total_rows += upsert_daily_prices(conn, records, asset_keys)
        failed_all += failed

        done = min(b + args.batch_size, len(tickers))
        elapsed = time.monotonic() - started
        eta = elapsed / done * (len(tickers) - done)
        print(f"[{done}/{len(tickers)}] +{len(records):,} rows, {len(failed)} failed in batch | "
              f"total {total_rows:,} rows | elapsed {elapsed / 60:.1f} min, ETA {eta / 60:.1f} min", flush=True)
        if done < len(tickers):
            time.sleep(args.batch_sleep)

    print(f"\nDone: {total_rows:,} rows upserted; {len(failed_all)} tickers failed", flush=True)
    if failed_all:
        Path(args.failed_file).parent.mkdir(parents=True, exist_ok=True)
        Path(args.failed_file).write_text("\n".join(failed_all) + "\n")
        print(f"failed tickers written to {args.failed_file} (re-run with --only-missing to retry them)")

    if args.prune_short_history:
        removed = prune_short_history(engine, args.prune_short_history, universes or ["tase", "sp400", "sp600"])
        print(f"pruned {len(removed)} tickers with < {args.prune_short_history} trading days"
              + (f": {', '.join(removed[:25])}{' ...' if len(removed) > 25 else ''}" if removed else ""))


if __name__ == "__main__":
    main()
