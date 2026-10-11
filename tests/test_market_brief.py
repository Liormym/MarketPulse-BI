"""Daily Macro Brief: the pure text generator."""
import re

from marketpulse.market_brief import build_brief


def _facts(**over):
    sectors = {
        t: {"name": n, "change_pct": 0.1}
        for t, n in [
            ("XLK", "Technology"), ("XLF", "Financials"), ("XLE", "Energy"), ("XLV", "Healthcare"),
            ("XLY", "Consumer Discretionary"), ("XLP", "Consumer Staples"), ("XLI", "Industrials"),
        ]
    }
    base = {
        "market_date": "2026-10-07",
        "ten_year": {"yield": 4.2, "change_pct": 0.0},
        "oil": {"price": 80.0, "change_pct": 0.1},
        "sp500_change_pct": 0.1,
        "nasdaq_change_pct": 0.1,
        "rsp_change_pct": 0.1,
        "sectors": sectors,
        "breadth": {"pct_up": 50.0, "n": 500},
    }
    base.update(over)
    return base


def _sentences(text):
    return [s for s in re.split(r"(?<=\.)\s+", text.strip()) if s]


def test_quiet_day_is_still_a_valid_one_sentence_brief():
    out = build_brief(_facts())
    assert 1 <= len(_sentences(out)) <= 2
    assert "little changed" in out and "holds near 4.20%" in out


def test_yield_crossing_a_milestone_is_named():
    out = build_brief(_facts(ten_year={"yield": 4.52, "change_pct": 1.0}))  # +4.5 bps, crosses 4.5
    assert "climbs above 4.5%" in out


def test_falling_yield_and_selloff():
    out = build_brief(_facts(sp500_change_pct=-1.8, ten_year={"yield": 3.9, "change_pct": -1.5}))
    assert "sell off sharply" in out and "eases" in out


def test_sector_leader_laggard_and_plural_verbs():
    f = _facts()
    f["sectors"]["XLE"]["change_pct"] = 3.1
    f["sectors"]["XLK"]["change_pct"] = -1.4
    f["sectors"]["XLF"]["change_pct"] = 2.9
    out = build_brief(f)
    assert "Energy surges (+3.1%)" in out
    assert "Technology faces pressure (-1.4%)" in out
    f["sectors"]["XLE"]["change_pct"] = 0.1
    assert "Financials surge (+2.9%)" in build_brief(f)


def test_oil_and_breadth_and_narrow_leadership():
    out = build_brief(_facts(oil={"price": 91.4, "change_pct": 3.2}, breadth={"pct_up": 20.0, "n": 500}))
    assert "crude oil jumps 3.2% to $91" in out.lower() and "breadth is weak" in out
    narrow = build_brief(_facts(sp500_change_pct=1.0, rsp_change_pct=0.2))
    assert "narrow" in narrow


def test_never_longer_than_two_sentences_and_tolerates_missing_data():
    f = _facts(sp500_change_pct=2.0, oil={"price": 95.0, "change_pct": -4.0})
    f["sectors"]["XLE"]["change_pct"] = -3.0
    assert len(_sentences(build_brief(f))) <= 2
    empty = build_brief({"sectors": {}, "ten_year": None, "oil": None, "breadth": None})
    assert empty.startswith("Markets are quiet")
    assert build_brief({"ten_year": {"yield": None, "change_pct": None}, "sectors": {}}) == empty
