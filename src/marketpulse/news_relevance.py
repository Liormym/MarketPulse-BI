"""Is a headline actually ABOUT a given stock?

The news search returns sector roundups, market wrap-ups and stories about
competitors ("Nasdaq sinks 345 points" says nothing about Chevron). A headline
is only linked to a stock when it explicitly names the company (full or
distinctive short name) or the ticker; see mentions_stock(). Used both when
fetching (off-topic items are never stored) and when picking the Recent
Company News panel.
"""
from __future__ import annotations

import re

_LEGAL_SUFFIXES = {
    "inc", "incorporated", "corp", "corporation", "co", "company", "companies", "ltd", "limited",
    "plc", "holdings", "holding", "group", "the", "class", "a", "b", "c", "lp", "llc", "nv", "sa", "ag",
}
# Too common to identify a company on their own as a first word ("General Mills" vs "General Motors").
_GENERIC_FIRST_WORDS = {
    "american", "general", "global", "united", "national", "first", "southern", "western", "eastern",
    "international", "digital", "financial", "capital", "energy", "health", "consumer", "interactive",
    "advanced", "applied", "western", "pacific", "atlantic", "northern", "central", "public", "great",
}
_COMPARISON = re.compile(r"\b(vs\.?|versus)\b", re.IGNORECASE)


def _company_names(company_name: str) -> tuple[str | None, str | None]:
    """(full cleaned name, distinctive first word or None)."""
    cleaned = re.sub(r"\([^)]*\)", " ", company_name or "")
    words = [w for w in re.split(r"[^A-Za-z0-9']+", cleaned) if w]
    while words and words[-1].lower().strip("'") in _LEGAL_SUFFIXES:
        words.pop()
    if not words:
        return None, None
    full = " ".join(words).lower().replace("'", "")
    first = words[0].lower().replace("'", "")
    first_ok = len(first) >= 4 and first not in _GENERIC_FIRST_WORDS and len(words) > 1
    return full, (first if first_ok else None)


def _plain(text: str) -> str:
    """Lower-cased text in two forms joined by a separator, so a company name
    matches either way: apostrophes dropped ("McDonald's" -> "mcdonalds") and
    possessives dropped ("Nvidia's" -> "nvidia")."""
    low = text.replace("\u2019", "'").lower()
    return re.sub(r"['\u2019]", "", low) + " | " + re.sub(r"'s\b", "", low).replace("'", "")


def company_name_for_search(company_name: str) -> str:
    """Name as people write it in headlines: legal suffixes and "(The)" removed."""
    cleaned = re.sub(r"\([^)]*\)", " ", company_name or "")
    words = [w for w in re.split(r"\s+", cleaned.strip()) if w]
    while words and words[-1].lower().strip(".,'") in _LEGAL_SUFFIXES:
        words.pop()
    return " ".join(w.strip(",") for w in words)


def normalize_title(title: str) -> str:
    """Lower-cased alphanumerics only, without a trailing " - Publisher"."""
    core = re.sub(r"\s+[-\u2013\u2014|]\s+[^-\u2013\u2014|]{2,40}$", "", title.strip())
    return re.sub(r"[^a-z0-9]+", " ", core.lower()).strip()


_PUBLISHER_SUFFIX = re.compile(r"\s+[-\u2013\u2014|]\s+[^-\u2013\u2014|]{2,40}$")


def strip_publisher(title: str) -> str:
    """Google News appends " - Publisher"; the publisher is not part of the story."""
    return _PUBLISHER_SUFFIX.sub("", title.strip())


def mentions_stock(title: str, ticker: str, company_name: str) -> bool:
    """Strict filter for the Recent Company News panel: the headline itself must
    explicitly name the company (full or distinctive short name) or the ticker.
    The publisher suffix is ignored (a "- Nvidia Newsroom" byline must not make
    a story about someone else count) and head-to-head comparisons are
    dropped, since the story is not about this one stock."""
    core = strip_publisher(title)
    if _COMPARISON.search(core):
        return False
    plain = _plain(core)
    full, first = _company_names(company_name)
    for name in (full, first):
        if name and re.search(rf"(?<![a-z0-9]){re.escape(name)}(?![a-z0-9])", plain):
            return True
    t = re.escape(ticker)
    if re.search(rf"(\(|\$|(NASDAQ|NYSE|AMEX|NYSEARCA|TSX)\s*:\s*){t}(\)|\b)", core):
        return True
    return len(ticker) >= 4 and re.search(rf"(?<![A-Za-z0-9]){t}(?![A-Za-z0-9])", core) is not None


# Auto-generated institutional-holdings notices ("Shares Bought by X Capital",
# "Stock Position Raised by Y LLC"): about the stock only in name, they carry no
# news and would crowd real stories out of a four-item panel.
_LOW_SIGNAL = re.compile(
    r"\b(shares?|stock|position|stake|holdings?)\b.{0,40}\b(bought|sold|acquired|purchased|raised|decreased|lowered|trimmed|boosted|cut|added|reduced|increased)\b"
    r"|\b(acquires|buys|sells|boosts|lowers|trims|raises|cuts|adds|reduces)\b.{0,25}\b(stake|position|holdings?|shares)\b",
    re.IGNORECASE,
)


def is_low_signal(title: str) -> bool:
    return bool(_LOW_SIGNAL.search(strip_publisher(title)))


def publisher_of(title: str) -> str | None:
    """The " - Publisher" Google News appends, if any."""
    m = re.search(r"\s+[-\u2013\u2014|]\s+([^-\u2013\u2014|]{2,40})$", title.strip())
    return m.group(1).strip() if m else None
