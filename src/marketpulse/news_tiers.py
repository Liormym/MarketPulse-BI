"""Tiered news fetching, so ~2,000 tickers never mean ~2,000 feed requests per run.

News is fetched from public RSS feeds with no API key, and those feeds throttle
and ban abusive clients. The scheduled pipeline runs three times a day; fetching
every ticker every run would be ~6,000 requests/day for a universe where most
tickers have a headline or two a week. So each universe gets a "max age": a
ticker's news is only re-fetched when its last successful fetch (StockDataFreshness.
NewsFetchedAt) is older than its universe's max age.

    CORE tiers   max age 0     fetched on EVERY run (S&P 500 + personal holdings:
                               the names people actually open)
    CATCH-UP     max age N h   fetched when older than N hours (S&P 400/600, TASE)

Catch-up tickers are processed most-overdue first (never-fetched first of all),
and at most `catchup_budget` of them per run, so even a cold start with 1,400
never-fetched tickers spreads over several runs instead of one burst. Core
tickers are never starved by the budget. A ticker whose fetch failed keeps its
old timestamp, so it is simply due again next run: no state to repair.
"""
from __future__ import annotations

from datetime import datetime, timedelta


def parse_news_tiers(spec: str) -> dict[str, float]:
    """"sp500:0,personal:0,sp400:24" -> {"sp500": 0.0, "personal": 0.0, "sp400": 24.0} (hours)."""
    tiers: dict[str, float] = {}
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        universe, _, hours = part.partition(":")
        try:
            tiers[universe.strip()] = float(hours)
        except ValueError:
            raise ValueError(f"bad news tier {part!r}: expected 'universe:max_age_hours'") from None
        if tiers[universe.strip()] < 0:
            raise ValueError(f"bad news tier {part!r}: max age cannot be negative")
    return tiers


def select_news_due(
    assets: list[dict],
    last_fetched: dict[str, datetime | None],
    now: datetime,
    tiers: dict[str, float],
    catchup_budget: int,
) -> tuple[list[dict], dict[str, int]]:
    """assets: [{'ticker', 'company_name', 'universe'}]. last_fetched: ticker ->
    last successful news fetch (None = never). Returns (assets to fetch now,
    counts {'core', 'catchup', 'deferred', 'not_due', 'untiered'})."""
    core: list[dict] = []
    overdue: list[tuple[float, dict]] = []  # (hours overdue, asset); never-fetched sorts first
    counts = {"core": 0, "catchup": 0, "deferred": 0, "not_due": 0, "untiered": 0}

    for asset in assets:
        max_age = tiers.get(asset["universe"])
        if max_age is None:
            counts["untiered"] += 1  # etf / index / crypto: no company news
            continue
        if max_age == 0:
            core.append(asset)
            continue
        fetched_at = last_fetched.get(asset["ticker"])
        if fetched_at is None:
            overdue.append((float("inf"), asset))
            continue
        age = now - fetched_at
        if age >= timedelta(hours=max_age):
            overdue.append(((age - timedelta(hours=max_age)).total_seconds() / 3600, asset))
        else:
            counts["not_due"] += 1

    overdue.sort(key=lambda pair: (-pair[0], pair[1]["ticker"]))
    chosen = [a for _, a in overdue[: max(catchup_budget, 0)]]
    counts["core"] = len(core)
    counts["catchup"] = len(chosen)
    counts["deferred"] = len(overdue) - len(chosen)
    return core + chosen, counts
