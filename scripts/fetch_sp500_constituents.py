"""One-time (re-runnable) scope expansion: scrapes the current S&P 500
constituent list from Wikipedia and merges it into config/watchlist.yaml.

Merge, not replace: Lior's existing 82 tickers include real personal
holdings that aren't S&P 500 members (crypto, TASE stocks, benchmark
indices, ETFs) — those are left untouched. Only S&P 500 tickers not
already present are added; a ticker already in the watchlist keeps its
existing (already human-verified) company_name/sector.

Usage: .venv/bin/python scripts/fetch_sp500_constituents.py
"""
import io
import sys
from pathlib import Path

import pandas as pd
import requests
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
WATCHLIST_PATH = REPO_ROOT / "config" / "watchlist.yaml"

WIKIPEDIA_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"

# Wikipedia's GICS Sector labels -> this project's existing sector naming
# convention (see the other 82 entries in watchlist.yaml). Anything not
# listed here (Consumer Staples, Materials, Real Estate) is new to this
# watchlist but kept as-is: it's a legitimate GICS sector, just one none of
# the original 82 happened to be in.
SECTOR_MAP = {
    "Information Technology": "Technology",
    "Health Care": "Healthcare",
}


def fetch_sp500_table() -> pd.DataFrame:
    # Wikipedia blocks the default urllib UA pandas.read_html uses under the
    # hood (403), so fetch with a real UA first and hand read_html the HTML.
    headers = {"User-Agent": "Mozilla/5.0 MarketPulseBI-research-script/1.0"}
    resp = requests.get(WIKIPEDIA_URL, headers=headers, timeout=15)
    resp.raise_for_status()
    tables = pd.read_html(io.StringIO(resp.text), attrs={"id": "constituents"})
    return tables[0]


class _IndentedListDumper(yaml.Dumper):
    """Matches the original watchlist.yaml style: list items indented under
    their parent key, instead of PyYAML's default flush-left block sequences.
    """

    def increase_indent(self, flow=False, indentless=False):
        return super().increase_indent(flow, False)


def to_yfinance_ticker(wikipedia_symbol: str) -> str:
    # Wikipedia uses a dot for share classes (BRK.B); yfinance/Yahoo Finance
    # uses a hyphen (BRK-B).
    return wikipedia_symbol.replace(".", "-")


def main() -> None:
    watchlist = yaml.safe_load(WATCHLIST_PATH.read_text())
    existing = {a["ticker"]: a for a in watchlist["assets"]}
    existing_count = len(existing)

    print("Fetching S&P 500 constituent list from Wikipedia...")
    df = fetch_sp500_table()
    print(f"Got {len(df)} S&P 500 rows")

    added = 0
    for _, row in df.iterrows():
        ticker = to_yfinance_ticker(str(row["Symbol"]).strip())
        if ticker in existing:
            continue  # keep the already-verified entry as-is
        sector = SECTOR_MAP.get(row["GICS Sector"], row["GICS Sector"])
        existing[ticker] = {
            "ticker": ticker,
            "company_name": str(row["Security"]).strip(),
            "sector": sector,
        }
        added += 1

    merged = [existing[t] for t in sorted(existing.keys())]

    watchlist["assets"] = merged
    with WATCHLIST_PATH.open("w") as f:
        f.write(
            "# MarketPulse BI watchlist — Lior's real personal holdings "
            "(stocks, ETFs, a benchmark index, and\n"
            "# two crypto pairs) merged with the full S&P 500 index "
            "constituent list, scraped from Wikipedia\n"
            f"# ({WIKIPEDIA_URL}). S&P 500 tickers already present from the "
            "personal-holdings list keep their\n"
            "# original, human-verified company_name/sector; only new "
            "tickers were added by the scrape.\n"
        )
        yaml.dump(
            {"assets": merged},
            f,
            Dumper=_IndentedListDumper,
            default_flow_style=False,
            sort_keys=False,
            allow_unicode=True,
        )

    print(f"{existing_count} existing tickers kept, {added} new S&P 500 tickers added")
    print(f"Total watchlist size: {len(merged)}")


if __name__ == "__main__":
    main()
