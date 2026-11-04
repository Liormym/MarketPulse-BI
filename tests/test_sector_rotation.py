"""Early Sector Rotation Engine: the pure signal, regime and breadth maths."""
import pytest

from marketpulse.sector_rotation import (
    REGIME_BALANCED,
    REGIME_INFLOW,
    REGIME_OUTFLOW,
    align_closes,
    compute_sector_rotation_rows,
    count_breadth,
    relative_return_pp,
    rotation_signal,
)


def test_no_relative_returns_means_no_read():
    r = rotation_signal(None, 1.0, 60.0)
    assert r.score is None and r.regime is None


def test_signal_is_bounded_and_signed():
    assert rotation_signal(10, 10, 100).score == 1.0
    assert rotation_signal(-10, -10, 0).score == -1.0
    assert rotation_signal(0, 0, 50).score == 0.0


def test_confirmed_outflow_needs_negative_2d_5d_and_more_decliners_than_advancers():
    assert rotation_signal(-2.0, -4.0, 20.0).regime == REGIME_OUTFLOW
    assert rotation_signal(-2.0, -4.0, 55.0).regime == REGIME_BALANCED  # breadth disagrees
    assert rotation_signal(-2.0, 0.5, 20.0).regime == REGIME_BALANCED  # 5-day disagrees
    assert rotation_signal(0.5, -4.0, 20.0).regime == REGIME_BALANCED  # 2-day disagrees
    assert rotation_signal(-0.2, -0.4, 45.0).regime == REGIME_BALANCED  # agreement but too weak


def test_confirmed_inflow_mirrors_outflow():
    assert rotation_signal(2.0, 4.0, 80.0).regime == REGIME_INFLOW
    assert rotation_signal(2.0, 4.0, 45.0).regime == REGIME_BALANCED


def test_without_breadth_the_signal_still_reads_but_never_names_a_regime():
    r = rotation_signal(-3.0, -6.0, None)
    assert r.score == pytest.approx(-1.0) and r.regime == REGIME_BALANCED


def test_alignment_uses_dates_present_in_both_series():
    sector = [(1, 10.0), (2, 11.0), (3, 12.0), (5, 13.0)]
    bench = [(1, 100.0), (3, 110.0), (4, 111.0), (5, 120.0)]
    dates, s, b = align_closes(sector, bench)
    assert dates == [1, 3, 5] and s == [10.0, 12.0, 13.0] and b == [100.0, 110.0, 120.0]


def test_relative_return_is_in_percentage_points_vs_the_benchmark():
    # sector +10% over 2 bars, index +4% -> +6 pp
    assert relative_return_pp([100, 105, 110], [100, 102, 104], 2) == pytest.approx(6.0)
    assert relative_return_pp([100, 110], [100, 104], 2) is None  # not enough history


def test_breadth_ignores_unchanged_and_needs_enough_movers():
    rows = [(11, 10), (12, 10), (9, 10), (10, 10), (13, 10), (14, 10), (8, 10)]
    adv, dec, pct = count_breadth(rows)
    assert (adv, dec, pct) == (4, 2, pytest.approx(66.7, abs=0.1))
    assert count_breadth([(11, 10), (9, 10)])[2] is None


def test_full_row_uses_the_latest_shared_date_and_that_dates_breadth():
    sector = {"XLK": [(d, 100.0 - i * 2) for i, d in enumerate(range(20260101, 20260130))]}  # falling
    sector["XLK"].sort()
    bench = [(d, 100.0) for d, _ in sector["XLK"]]
    as_of = sector["XLK"][-1][0]
    stocks = {"XLK": {k: [(as_of - 1, 10.0), (as_of, 9.0)] for k in range(8)}}  # all 8 fell on the day
    (row,) = compute_sector_rotation_rows(sector, bench, stocks)
    assert row["ticker"] == "XLK" and row["date_key"] == as_of
    assert row["rel_2d_pct"] < 0 and row["rel_5d_pct"] < 0 and row["rel_20d_pct"] < 0
    assert (row["advancing"], row["declining"], row["breadth_pct"]) == (0, 8, 0.0)
    assert row["regime"] == REGIME_OUTFLOW


# ---- benchmark: equal-weight RSP, not the cap-weighted S&P 500 ----
def test_an_equal_weight_benchmark_makes_relative_returns_sum_to_about_zero():
    """The point of the switch. 11 sectors, a falling cap-weighted index (mega-caps drop while
    every other sector holds or rises) vs an equal-weight index (the plain average of the
    sectors): against the cap-weighted index EVERY non-tech sector looks like a leader; against
    the equal-weight one the relative returns net out to ~0, so a signal means real rotation."""
    closes = {f"S{i}": [100.0, 100.0, 100.0 + i * 0.5] for i in range(10)}  # ten steady/rising sectors
    closes["TECH"] = [100.0, 100.0, 90.0]  # the mega-cap-heavy sector falls 10%

    cap_weighted = [100.0, 100.0, 96.0]  # index is mostly tech: falls ~4%
    n = len(closes)
    equal_weight = [sum(s[k] for s in closes.values()) / n for k in range(3)]

    vs_cap = {t: relative_return_pp(s, cap_weighted, 2) for t, s in closes.items()}
    vs_equal = {t: relative_return_pp(s, equal_weight, 2) for t, s in closes.items()}

    assert sum(v > 0 for v in vs_cap.values()) == 10  # "10 of 11 sectors lead": falsely bullish
    assert sum(vs_equal.values()) / n == pytest.approx(0.0, abs=0.35)  # ~0 average: genuine relative flow
    assert vs_equal["TECH"] < 0 < vs_equal["S9"]  # and the actual laggard / leader still stand out


def test_the_engine_reads_rsp():
    from marketpulse.sector_rotation import BENCHMARK_COLUMN, BENCHMARK_LABEL

    assert BENCHMARK_COLUMN == "RSPPrice" and "RSP" in BENCHMARK_LABEL


@pytest.mark.populated_db
def test_stored_rotation_is_measured_against_rsp_not_the_sp500():
    """Recomputes inside a rolled-back transaction and checks one sector against an independent
    query on MacroIndicators.RSPPrice (and that the S&P 500 series would give a different number)."""
    from sqlalchemy import text

    from marketpulse.load.db import get_engine
    from marketpulse.sector_rotation import compute_and_store_sector_rotation

    with get_engine().connect() as conn:
        trans = conn.begin()
        try:
            rows = {r["ticker"]: r for r in compute_and_store_sector_rotation(conn)}
            xlk = rows["XLK"]
            as_of = xlk["date_key"]

            for col in ("RSPPrice", "SP500Index"):
                series = {
                    dk: v for dk, v in conn.execute(
                        text(f'SELECT "DateKey", "{col}" FROM "MacroIndicators" WHERE "{col}" IS NOT NULL AND "DateKey" <= :d ORDER BY "DateKey" DESC LIMIT 40'),
                        {"d": as_of},
                    )
                }
                etf = {
                    dk: v for dk, v in conn.execute(
                        text('SELECT p."DateKey", p."Close" FROM "FactDailyPrice" p JOIN "DimAsset" a ON a."AssetKey" = p."AssetKey" '
                             'WHERE a."Ticker" = \'XLK\' AND p."DateKey" <= :d ORDER BY p."DateKey" DESC LIMIT 40'),
                        {"d": as_of},
                    )
                }
                common = sorted(set(series) & set(etf))
                got = relative_return_pp([etf[d] for d in common], [series[d] for d in common], 5)
                if col == "RSPPrice":
                    assert xlk["rel_5d_pct"] == pytest.approx(got, abs=0.01)
                else:
                    assert xlk["rel_5d_pct"] != pytest.approx(got, abs=0.01)
        finally:
            trans.rollback()
