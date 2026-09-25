import numpy as np

from marketpulse.pattern_detection import (
    CUP_AND_HANDLE_LOOKBACK,
    HEAD_AND_SHOULDERS_LOOKBACK,
    detect_cup_and_handle,
    detect_head_and_shoulders,
    detect_patterns,
)


def _head_and_shoulders_series():
    seg1 = np.linspace(100, 110, 10)  # rise to left shoulder
    seg2 = np.linspace(110, 95, 8)  # fall to trough
    seg3 = np.linspace(95, 130, 10)  # rise to head
    seg4 = np.linspace(130, 95, 10)  # fall to trough
    seg5 = np.linspace(95, 108, 8)  # rise to right shoulder
    seg6 = np.linspace(108, 90, 14)  # fall off
    return np.concatenate([seg1, seg2, seg3, seg4, seg5, seg6]).tolist()


def _cup_and_handle_series():
    seg1 = np.linspace(95, 110, 15)  # rise to left rim
    seg2 = np.linspace(110, 80, 20)  # fall to cup bottom
    seg3 = np.linspace(80, 110, 20)  # recover to right rim
    seg4 = np.linspace(110, 100, 10)  # handle: shallow pullback
    seg5 = np.linspace(100, 105, 25)  # drift after the handle
    return np.concatenate([seg1, seg2, seg3, seg4, seg5]).tolist()


def _flat_series(n):
    rng = np.random.default_rng(42)
    return (100 + rng.normal(0, 0.5, n)).tolist()


def test_head_and_shoulders_detected_on_synthetic_series():
    result = detect_head_and_shoulders(_head_and_shoulders_series())
    assert result is not None
    assert result.name == "Head and Shoulders"


def test_head_and_shoulders_none_on_flat_series():
    assert detect_head_and_shoulders(_flat_series(HEAD_AND_SHOULDERS_LOOKBACK)) is None


def test_head_and_shoulders_none_on_insufficient_history():
    assert detect_head_and_shoulders([100.0] * (HEAD_AND_SHOULDERS_LOOKBACK - 1)) is None


def test_cup_and_handle_detected_on_synthetic_series():
    result = detect_cup_and_handle(_cup_and_handle_series())
    assert result is not None
    assert result.name == "Cup and Handle"


def test_cup_and_handle_none_on_flat_series():
    assert detect_cup_and_handle(_flat_series(CUP_AND_HANDLE_LOOKBACK)) is None


def test_cup_and_handle_none_on_insufficient_history():
    assert detect_cup_and_handle([100.0] * (CUP_AND_HANDLE_LOOKBACK - 1)) is None


def test_detect_patterns_aggregates_all_hints():
    # A series long enough to be checked by both detectors, but shaped like
    # neither - should yield no hints.
    closes = _flat_series(CUP_AND_HANDLE_LOOKBACK)
    assert detect_patterns(closes) == []


def test_detect_patterns_finds_head_and_shoulders_when_present():
    closes = _head_and_shoulders_series()
    hints = detect_patterns(closes)
    assert any(h.name == "Head and Shoulders" for h in hints)
