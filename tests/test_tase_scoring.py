"""Israeli stocks: neutral sector pillar with an explicit note; no short-interest / insider inputs."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "webapp"))

import db  # noqa: E402
from marketpulse.scoring import SECTOR_MAX, compute_investment_score  # noqa: E402

N = 260


def _data(universe):
    flat = [100.0] * N
    return dict(
        universe=universe, prices=flat, sma20=flat, sma50=flat, sma200=flat, rsi14=50.0, rsi14_prev=50.0,
        atr14=1.0, atr_90d_avg=1.0, atr_90d_std=0.2,
    )


SECTOR = dict(flow_status="Distribution", rel_20d_pct=-5.0, rel_2d_pct=-3.0, rel_5d_pct=-6.0, breadth_pct=10.0, breadth_median_pct=60.0)
ENRICHMENT = dict(short_percent_of_float=0.25, insider_net="selling")


def test_tase_kwargs_ignore_sector_short_interest_and_insider_data():
    kw = db.build_score_kwargs(_data("tase"), SECTOR, ENRICHMENT)
    assert kw["sector_na_reason"] == "No sector ETF flow data available for TASE"
    assert kw["short_percent_of_float"] is None and kw["insider_net"] is None


def test_tase_score_has_the_neutral_sector_pillar_whatever_the_cache_holds():
    r = compute_investment_score(**db.build_score_kwargs(_data("tase"), SECTOR, ENRICHMENT))
    assert r.sector_points == SECTOR_MAX / 2
    assert "sector_outflow" not in r.flags and "insider_selling" not in r.flags


def test_us_kwargs_pass_everything_through():
    kw = db.build_score_kwargs(_data("sp500"), SECTOR, ENRICHMENT)
    assert kw["sector_na_reason"] is None
    assert (kw["sector_rel_2d_pct"], kw["sector_rel_5d_pct"], kw["sector_breadth_pct"]) == (-3.0, -6.0, 10.0)
    assert kw["sector_breadth_median_pct"] == 60.0
    assert kw["short_percent_of_float"] == 0.25 and kw["insider_net"] == "selling"
    assert compute_investment_score(**kw).flags.get("sector_outflow") is True
