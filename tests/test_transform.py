from datetime import date

from marketpulse.transform import clean_headline, date_to_key


def test_date_to_key():
    assert date_to_key(date(2026, 3, 5)) == 20260305


def test_clean_headline_collapses_whitespace():
    assert clean_headline("Apple   beats\n\testimates") == "Apple beats estimates"


def test_clean_headline_strips_control_chars():
    assert clean_headline("Tesla surges\x00 today") == "Tesla surges today"


def test_clean_headline_strips_surrounding_whitespace():
    assert clean_headline("   Fed holds rates   ") == "Fed holds rates"
