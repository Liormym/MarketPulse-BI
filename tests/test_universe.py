import pandas as pd
import pytest

from marketpulse.extract.prices import _records_from_history
from marketpulse.universe import (
    classify_currency,
    infer_universe,
    merge_index_membership,
    select_tase,
    tase_turnover_ils,
    to_yahoo_ticker,
)


def test_agorot_are_recognised_by_yahoo_currency_not_by_suffix():
    assert classify_currency("ILA") == ("ILS", 100)  # agorot -> whole shekels
    assert classify_currency("ILS") == ("ILS", 1)  # e.g. the TA-35 index
    assert classify_currency("USD") == ("USD", 1)
    assert classify_currency("GBp") is None  # pence: not supported, so skipped rather than mis-scaled
    assert classify_currency(None) is None


def test_prices_are_divided_by_the_divisor_but_volume_is_not():
    idx = pd.to_datetime(["2026-10-12", "2026-10-13"])
    hist = pd.DataFrame(
        {"Open": [700.0, 705.0], "High": [710.0, 712.0], "Low": [698.0, 700.0], "Close": [707.5, 709.0], "Volume": [1000, 2000]},
        index=idx,
    )
    agorot = _records_from_history("BEZQ.TA", hist, 100)
    assert [round(r.close, 4) for r in agorot] == [7.075, 7.09]
    assert (agorot[0].open, agorot[0].high, agorot[0].low) == (7.0, 7.1, 6.98)
    assert [r.volume for r in agorot] == [1000, 2000]
    plain = _records_from_history("AAPL", hist)
    assert plain[0].close == 707.5


def test_share_class_symbols_and_index_priority():
    assert to_yahoo_ticker("BRK.B") == "BRK-B"
    m = merge_index_membership(
        ("sp500", [("AAPL", "Apple", "Information Technology"), ("BRK.B", "Berkshire", "Financials")]),
        ("sp400", [("AAPL", "Apple dup", "Information Technology"), ("ACM", "AECOM", "Industrials")]),
    )
    assert m["AAPL"] == {"company_name": "Apple", "sector": "Technology", "universe": "sp500"}  # largest index wins
    assert m["BRK-B"]["universe"] == "sp500" and m["ACM"]["universe"] == "sp400"


def test_universe_inference_order():
    members = {"AAPL": "sp500"}
    assert infer_universe("AAPL", "Technology", members) == "sp500"
    assert infer_universe("SPY", "ETF", members) == "etf"
    assert infer_universe("^KS11", "Index", members) == "index"
    assert infer_universe("BTC-USD", "Crypto", members) == "crypto"
    assert infer_universe("BEZQ.TA", "Telecom", members) == "tase"
    assert infer_universe("CVNA", "Consumer Discretionary", members) == "personal"


def _q(symbol, ccy="ILA", price=1000.0, vol=10_000, typ="EQUITY", **kw):
    return {"symbol": symbol, "quoteType": typ, "currency": ccy, "regularMarketPrice": price,
            "averageDailyVolume3Month": vol, "shortName": symbol, **kw}


def test_tase_turnover_converts_agorot_to_shekels():
    assert tase_turnover_ils(_q("X.TA", "ILA", price=1000.0, vol=10_000)) == 100_000  # 10 shekels x 10k shares
    assert tase_turnover_ils(_q("Y.TA", "ILS", price=10.0, vol=10_000)) == 100_000
    assert tase_turnover_ils(_q("Z.TA", "GBp")) == 0.0


def test_tase_selection_filters_and_reports_why():
    quotes = [
        _q("GOOD.TA"),  # 100k/day
        _q("THIN.TA", vol=10),  # illiquid
        _q("FUND.TA", typ="MUTUALFUND"),
        _q("US-LISTED", ccy="USD"),  # not a .TA symbol
        _q("PENCE.TA", ccy="GBp"),
        _q("DUP.TA"),
        _q("DUP.TA"),
    ]
    entries, dropped = select_tase(quotes, min_turnover_ils=50_000)
    assert [e["ticker"] for e in entries] == ["DUP.TA", "GOOD.TA"]
    assert entries[0]["price_divisor"] == 100 and entries[0]["currency"] == "ILS"
    assert dropped == {"not_equity": 1, "not_tase_symbol": 1, "unsupported_currency": 1, "illiquid": 1}
