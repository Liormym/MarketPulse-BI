"""CEO/CFO insider activity from SEC Form 4 data (via yfinance).

Only open-market SALES and PURCHASES by the company's CEO or CFO count. Stock
awards/grants, gifts, option exercises and conversions are not trading
decisions and are ignored. The 90-day net direction (more sold than bought, or
the reverse) becomes the Investment Score's explicit -5 / 0 / +5 insider
modifier, so the badge, the rationale line and the score move together.
"""
from __future__ import annotations

import re
from datetime import date, timedelta

INSIDER_LOOKBACK_DAYS = 90

_CEO_CFO = re.compile(r"\b(ceo|cfo|chief executive|chief financial)\b", re.IGNORECASE)


def classify_transaction(raw_text: str | None) -> str | None:
    """'Sale' | 'Buy' | 'Gift' | 'Other', or None when yfinance gave no text
    (usually an unpriced option exercise or conversion)."""
    t = (raw_text or "").strip().lower()
    if t.startswith("sale"):
        return "Sale"
    if t.startswith("purchase"):
        return "Buy"
    if t.startswith("stock gift"):
        return "Gift"
    return "Other" if t else None


def is_ceo_cfo(position: str | None) -> bool:
    """Position text such as 'Chief Executive Officer', 'CFO', 'President and CEO'.
    A bare 'Officer', 'Director', COO, CTO or General Counsel does not qualify."""
    return bool(_CEO_CFO.search(position or ""))


def net_insider_activity(transactions: list[dict], today: date | None = None) -> dict:
    """transactions: dicts with is_executive_sale, is_executive_buy, value, shares,
    transaction_date. Returns {"net": "selling" | "buying" | None, sold/bought
    value and counts} over the last INSIDER_LOOKBACK_DAYS.

    Direction is decided by dollar value; if no transaction in the window has a
    value, by share count; an exact tie is neutral (None)."""
    cutoff = (today or date.today()) - timedelta(days=INSIDER_LOOKBACK_DAYS)
    sold_value = bought_value = 0.0
    sold_shares = bought_shares = 0
    sales = buys = 0
    for t in transactions:
        d = t.get("transaction_date")
        if d is None or d < cutoff:
            continue
        value = t.get("value") or 0.0
        shares = t.get("shares") or 0
        if t.get("is_executive_sale"):
            sales += 1
            sold_value += value
            sold_shares += shares
        elif t.get("is_executive_buy"):
            buys += 1
            bought_value += value
            bought_shares += shares

    if sold_value != bought_value:
        net = "selling" if sold_value > bought_value else "buying"
    elif sold_shares != bought_shares:
        net = "selling" if sold_shares > bought_shares else "buying"
    else:
        net = None
    return {
        "net": net,
        "sold_value": sold_value,
        "bought_value": bought_value,
        "sales": sales,
        "buys": buys,
    }
