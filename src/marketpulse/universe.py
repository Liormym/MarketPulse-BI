"""Stock-universe helpers: index membership, TASE selection, currency units.

Pure functions (no network, no DB) so they are unit-tested; scripts/build_universe.py
does the fetching and writes config/watchlist.yaml.

Currency units. Yahoo quotes most Tel Aviv Stock Exchange shares in AGOROT
(currency code "ILA", 1/100 of a shekel). classify_currency() maps a Yahoo currency
code to (stored currency, price divisor): "ILA" -> ("ILS", 100), so the pipeline
stores whole shekels and the asset is tagged ILS. A TASE instrument already quoted
in "ILS" (e.g. the TA-35 index) gets divisor 1. The tag comes from Yahoo's own
currency field, never from the ".TA" suffix alone.
"""
from __future__ import annotations

# Yahoo currency code -> (ISO currency stored in the DB, divisor applied to prices)
_MINOR_UNITS = {"ILA": ("ILS", 100)}
SUPPORTED_CURRENCIES = {"USD", "ILS"}

SP_INDEX_UNIVERSES = ("sp500", "sp400", "sp600")

# Wikipedia GICS labels -> this project's sector naming.
SECTOR_MAP = {"Information Technology": "Technology", "Health Care": "Healthcare"}

NON_EQUITY_SECTOR_UNIVERSE = {"ETF": "etf", "Index": "index", "Crypto": "crypto"}


def classify_currency(yahoo_currency: str | None) -> tuple[str, int] | None:
    """(currency, divisor) for a Yahoo currency code, or None if we do not support it
    (so the instrument is skipped rather than mixed in at the wrong scale)."""
    if not yahoo_currency:
        return None
    code, divisor = _MINOR_UNITS.get(yahoo_currency, (yahoo_currency.upper(), 1))
    return (code, divisor) if code in SUPPORTED_CURRENCIES else None


def to_yahoo_ticker(wikipedia_symbol: str) -> str:
    """Wikipedia writes share classes with a dot (BRK.B); Yahoo uses a hyphen (BRK-B)."""
    return wikipedia_symbol.strip().replace(".", "-")


def map_sector(gics_sector: str | None) -> str:
    return SECTOR_MAP.get(gics_sector or "", gics_sector or "Unclassified")


def infer_universe(ticker: str, sector: str | None, index_membership: dict[str, str]) -> str:
    """Universe tag for a watchlist entry. S&P membership wins; then instrument type;
    then TASE by suffix; everything else is one of the owner's personal holdings."""
    if ticker in index_membership:
        return index_membership[ticker]
    if sector in NON_EQUITY_SECTOR_UNIVERSE:
        return NON_EQUITY_SECTOR_UNIVERSE[sector]
    if ticker.endswith(".TA"):
        return "tase"
    return "personal"


def merge_index_membership(*tables: tuple[str, list[tuple[str, str, str]]]) -> dict[str, dict]:
    """tables: ("sp500", [(wikipedia_symbol, company, gics_sector), ...]), ...
    Returns {yahoo_ticker: {"company_name", "sector", "universe"}}. A name in several
    indices keeps the LARGEST one (sp500 over sp400 over sp600)."""
    out: dict[str, dict] = {}
    for universe, rows in tables:  # pass in priority order: sp500, sp400, sp600
        for symbol, company, gics in rows:
            t = to_yahoo_ticker(symbol)
            if t and t not in out:
                out[t] = {"company_name": str(company).strip(), "sector": map_sector(gics), "universe": universe}
    return out


def tase_turnover_ils(quote: dict) -> float:
    """Average daily traded value in shekels from a Yahoo screener quote: price (agorot
    converted) x 3-month average volume. 0 when it cannot be computed."""
    cls = classify_currency(quote.get("currency"))
    price = quote.get("regularMarketPrice") or quote.get("fulldayPrice")
    volume = quote.get("averageDailyVolume3Month") or quote.get("averageDailyVolume10Day") or 0
    if not cls or not price:
        return 0.0
    return float(price) / cls[1] * float(volume)


def select_tase(quotes: list[dict], min_turnover_ils: float) -> tuple[list[dict], dict[str, int]]:
    """Tradable TASE equities from a Yahoo screener result. Returns (entries, reasons for
    exclusion with counts). An entry: ticker, company_name, currency, price_divisor."""
    entries, dropped = {}, {"not_equity": 0, "not_tase_symbol": 0, "unsupported_currency": 0, "illiquid": 0}
    for q in quotes:
        symbol = (q.get("symbol") or "").strip()
        if q.get("quoteType") != "EQUITY":
            dropped["not_equity"] += 1
            continue
        if not symbol.endswith(".TA"):
            dropped["not_tase_symbol"] += 1
            continue
        cls = classify_currency(q.get("currency"))
        if cls is None:
            dropped["unsupported_currency"] += 1
            continue
        if tase_turnover_ils(q) < min_turnover_ils:
            dropped["illiquid"] += 1
            continue
        entries[symbol] = {
            "ticker": symbol,
            "company_name": (q.get("longName") or q.get("shortName") or symbol).strip()[:100],
            "currency": cls[0],
            "price_divisor": cls[1],
        }
    return sorted(entries.values(), key=lambda e: e["ticker"]), dropped
