"""Builds config/watchlist.yaml: the owner's personal holdings, the full S&P 1500
(S&P 500 + MidCap 400 + SmallCap 600, from Wikipedia) and the tradable Tel Aviv
Stock Exchange equities (Yahoo's screener, filtered).

Merge, not replace. An entry already in the watchlist keeps its human-verified
company_name/sector; it only gets its `universe` tag refreshed. New index members
are added. Re-runnable: running it again only adds/refreshes.

TASE filter (all of it is automatic, nothing hand-picked):
  * quoteType == EQUITY and a ".TA" symbol (Yahoo's screener already excludes funds
    and bonds from an equity query; this is belt and braces)
  * a currency we can store: "ILA" (agorot, divisor 100) or "ILS"
  * liquidity: average daily traded value >= --min-turnover-ils shekels
  * after the one-time history load, scripts/backfill_historical_prices.py
    --prune-short-history 200 drops anything with fewer than 200 trading days
    (the Investment Score needs 200).

Usage:
  .venv/bin/python scripts/build_universe.py [--min-turnover-ils 10000] [--dry-run]
"""
import argparse
import io
import sys
import time
from pathlib import Path

import pandas as pd
import requests
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from marketpulse.universe import (  # noqa: E402
    classify_currency,
    infer_universe,
    merge_index_membership,
    select_tase,
    tase_turnover_ils,
)

WATCHLIST_PATH = REPO_ROOT / "config" / "watchlist.yaml"
WIKI = {
    "sp500": "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
    "sp400": "https://en.wikipedia.org/wiki/List_of_S%26P_400_companies",
    "sp600": "https://en.wikipedia.org/wiki/List_of_S%26P_600_companies",
}
HEADERS = {"User-Agent": "Mozilla/5.0 MarketPulseBI-research-script/1.0"}
SCREENER_PAGE = 250
THRESHOLD_PREVIEW = (10_000, 25_000, 50_000, 100_000, 250_000, 500_000, 1_000_000)


class _IndentedListDumper(yaml.Dumper):
    def increase_indent(self, flow=False, indentless=False):
        return super().increase_indent(flow, False)


def fetch_wikipedia(universe: str) -> list[tuple[str, str, str]]:
    resp = requests.get(WIKI[universe], headers=HEADERS, timeout=20)
    resp.raise_for_status()
    table = pd.read_html(io.StringIO(resp.text), attrs={"id": "constituents"})[0]
    return [(r["Symbol"], r["Security"], r["GICS Sector"]) for _, r in table.iterrows()]


def fetch_tase_quotes() -> list[dict]:
    """Every equity Yahoo lists on the TLV exchange, paged politely with a retry/backoff."""
    import yfinance as yf

    query = yf.EquityQuery("eq", ["exchange", "TLV"])
    quotes, offset, total = [], 0, None
    while total is None or offset < total:
        for attempt in range(5):
            try:
                page = yf.screen(query, size=SCREENER_PAGE, offset=offset, sortField="intradaymarketcap", sortAsc=False)
                break
            except Exception as exc:  # noqa: BLE001
                wait = 15 * (2**attempt)
                print(f"  screener error ({exc}); retry in {wait}s", flush=True)
                time.sleep(wait)
        else:
            raise RuntimeError("Yahoo screener kept failing; try again later")
        total = page.get("total", 0)
        got = page.get("quotes", [])
        if not got:
            break
        quotes += got
        offset += SCREENER_PAGE
        time.sleep(2)  # be gentle between pages
    return quotes


def detect_currency(ticker: str):
    """(currency, divisor) from Yahoo's own currency field for a ticker the screener did not return."""
    import yfinance as yf

    try:
        return classify_currency(yf.Ticker(ticker).fast_info.get("currency"))
    except Exception:  # noqa: BLE001
        return None


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--min-turnover-ils", type=float, default=10_000, help="minimum average daily traded value, shekels (10k keeps ~490 of 902 TASE equities)")
    ap.add_argument("--dry-run", action="store_true", help="print the plan, write nothing")
    args = ap.parse_args()

    watchlist = yaml.safe_load(WATCHLIST_PATH.read_text())
    entries = {a["ticker"]: dict(a) for a in watchlist["assets"]}
    n_before = len(entries)

    print("S&P indices from Wikipedia...")
    membership = merge_index_membership(*[(u, fetch_wikipedia(u)) for u in ("sp500", "sp400", "sp600")])
    print("  " + ", ".join(f"{u}: {sum(1 for m in membership.values() if m['universe'] == u)}" for u in ("sp500", "sp400", "sp600")))
    index_universe = {t: m["universe"] for t, m in membership.items()}

    added_us = 0
    for t, m in membership.items():
        if t not in entries:
            entries[t] = {"ticker": t, "company_name": m["company_name"], "sector": m["sector"]}
            added_us += 1

    print("TASE equities from Yahoo's screener...")
    quotes = fetch_tase_quotes()
    print(f"  {len(quotes)} symbols returned")
    turnovers = sorted(tase_turnover_ils(q) for q in quotes if q.get("quoteType") == "EQUITY")
    print("  symbols with daily turnover >= :  " + "  ".join(
        f"{int(th / 1000)}k shekel: {sum(1 for v in turnovers if v >= th)}" for th in THRESHOLD_PREVIEW))
    tase, dropped = select_tase(quotes, args.min_turnover_ils)
    print(f"  kept {len(tase)} at >= {int(args.min_turnover_ils):,} shekels/day; dropped {dropped}")

    added_tase = 0
    for e in tase:
        cur = entries.get(e["ticker"])
        if cur is None:
            entries[e["ticker"]] = {**e, "sector": "Unclassified"}
            added_tase += 1
        else:  # already a watchlist entry: keep its name/sector, refresh currency facts
            cur["currency"], cur["price_divisor"] = e["currency"], e["price_divisor"]

    # Currency facts for TASE entries the screener did not cover (owner's holdings).
    for t, e in entries.items():
        if t.endswith(".TA") and "price_divisor" not in e:
            cls = detect_currency(t)
            if cls:
                e["currency"], e["price_divisor"] = cls
            else:
                print(f"  WARNING: could not determine the currency of {t}; it stays unconverted")

    for t, e in entries.items():
        e["universe"] = infer_universe(t, e.get("sector"), index_universe)
        if e["universe"] == "tase":
            e["exchange"] = "TLV"
            e.setdefault("currency", "ILS")

    ordered = []
    for t in sorted(entries):
        e = entries[t]
        out = {"ticker": t, "company_name": e["company_name"], "sector": e["sector"], "universe": e["universe"]}
        for k in ("exchange", "currency", "price_divisor"):
            if k in e:
                out[k] = e[k]
        ordered.append(out)

    counts = pd.Series([e["universe"] for e in ordered]).value_counts().to_dict()
    print(f"\nwatchlist: {n_before} -> {len(ordered)} (+{added_us} US, +{added_tase} TASE)   by universe: {counts}")
    agorot = sum(1 for e in ordered if e.get("price_divisor") == 100)
    print(f"TASE priced in agorot (divided by 100): {agorot}")
    if args.dry_run:
        print("dry run: nothing written")
        return

    with WATCHLIST_PATH.open("w") as f:
        f.write(
            "# MarketPulse BI watchlist, generated by scripts/build_universe.py.\n"
            "# Owner's personal holdings + S&P 1500 (Wikipedia) + tradable TASE equities (Yahoo screener).\n"
            "# Entries that predate the generator keep their human-verified company_name/sector.\n"
            "# universe: sp500|sp400|sp600|tase|personal|etf|index|crypto.  price_divisor 100 = Yahoo quotes the\n"
            "# instrument in agorot; the pipeline divides by it and stores whole shekels (currency ILS).\n"
        )
        yaml.dump({"assets": ordered}, f, Dumper=_IndentedListDumper, default_flow_style=False, sort_keys=False, allow_unicode=True)
    print(f"wrote {WATCHLIST_PATH.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    main()
