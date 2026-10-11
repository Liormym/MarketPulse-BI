from datetime import date, timedelta

import pytest

from marketpulse.insider import classify_transaction, is_ceo_cfo, net_insider_activity

TODAY = date(2026, 10, 11)


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Sale at price 163.45 per share.", "Sale"),
        ("Sale at price 12.30 - 12.80 per share.", "Sale"),
        ("Purchase at price 12.30 per share.", "Buy"),
        ("Purchase at price 100.00 - 101.50 per share.", "Buy"),
        ("Stock Gift at price 0 per share.", "Gift"),
        ("Stock Award(Grant) at price 0 per share.", "Other"),
        ("Conversion of Exercise of derivative security", "Other"),
        ("", None),
        (None, None),
    ],
)
def test_classify_transaction(text, expected):
    assert classify_transaction(text) == expected


@pytest.mark.parametrize(
    "position,expected",
    [
        ("Chief Executive Officer", True),
        ("Chief Financial Officer", True),
        ("President and CEO", True),
        ("CFO", True),
        ("Chief Operating Officer", False),
        ("Chief Technology Officer", False),
        ("General Counsel", False),
        ("Director", False),
        ("Officer", False),
        ("Officer and Director", False),
        (None, False),
    ],
)
def test_is_ceo_cfo_only_matches_the_two_roles(position, expected):
    assert is_ceo_cfo(position) is expected


def _t(days_ago, value, kind, shares=100):
    return {
        "transaction_date": TODAY - timedelta(days=days_ago),
        "value": value,
        "shares": shares,
        "is_executive_sale": kind == "sale",
        "is_executive_buy": kind == "buy",
    }


def test_net_direction_follows_dollar_value():
    assert net_insider_activity([_t(5, 1_000_000, "sale"), _t(10, 200_000, "buy")], TODAY)["net"] == "selling"
    assert net_insider_activity([_t(5, 1_000_000, "buy"), _t(10, 200_000, "sale")], TODAY)["net"] == "buying"


def test_buy_and_sell_of_equal_value_cancel_out():
    assert net_insider_activity([_t(5, 500_000, "sale"), _t(6, 500_000, "buy")], TODAY)["net"] is None


def test_only_the_last_90_days_count_and_non_executive_rows_are_ignored():
    old_only = [_t(91, 5_000_000, "sale")]
    assert net_insider_activity(old_only, TODAY)["net"] is None
    edge = [_t(90, 5_000_000, "sale")]
    assert net_insider_activity(edge, TODAY)["net"] == "selling"
    other = [{"transaction_date": TODAY, "value": 9e9, "shares": 1, "is_executive_sale": False, "is_executive_buy": False}]
    assert net_insider_activity(other, TODAY)["net"] is None
    assert net_insider_activity([], TODAY)["net"] is None


def test_falls_back_to_share_counts_when_no_values_are_reported():
    rows = [_t(5, None, "sale", shares=900), _t(6, None, "buy", shares=100)]
    assert net_insider_activity(rows, TODAY)["net"] == "selling"
    assert net_insider_activity(rows, TODAY)["sales"] == 1
