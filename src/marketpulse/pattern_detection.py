"""Lightweight heuristic chart-pattern hints.

CRITICAL: this is deliberately NOT wired into the Investment Score
(scoring.py) in any way. It exists purely to flag "you might want to open a
real charting tool and look closer" - a hint, not a signal the scoring
model trusts. These are simple geometric heuristics over closing price,
not a validated pattern-recognition model: false positives (and false
negatives) are expected. No neckline-break, volume-confirmation, or
statistical-significance testing is performed.
"""
from dataclasses import dataclass

import numpy as np
from scipy.signal import find_peaks

HEAD_AND_SHOULDERS_LOOKBACK = 60
HEAD_AND_SHOULDERS_SHOULDER_TOLERANCE_PCT = 7.0
CUP_AND_HANDLE_LOOKBACK = 90
CUP_AND_HANDLE_RIM_TOLERANCE_PCT = 5.0


@dataclass
class PatternHint:
    name: str
    description: str


def detect_head_and_shoulders(closes: list[float]) -> PatternHint | None:
    """Three peaks in the recent window where the middle one is tallest and
    the two shoulders are roughly the same height - the classic bearish
    reversal silhouette."""
    if len(closes) < HEAD_AND_SHOULDERS_LOOKBACK:
        return None
    window = np.array(closes[-HEAD_AND_SHOULDERS_LOOKBACK:])

    peaks, _ = find_peaks(window, distance=5, prominence=np.std(window) * 0.5)
    if len(peaks) < 3:
        return None

    p1, p2, p3 = peaks[-3], peaks[-2], peaks[-1]
    h1, h2, h3 = window[p1], window[p2], window[p3]
    if not (h2 > h1 and h2 > h3):
        return None

    shoulder_diff_pct = abs(h1 - h3) / ((h1 + h3) / 2) * 100
    if shoulder_diff_pct > HEAD_AND_SHOULDERS_SHOULDER_TOLERANCE_PCT:
        return None

    return PatternHint(
        name="Head and Shoulders",
        description=(
            f"Three peaks with a taller middle peak and shoulders within "
            f"{shoulder_diff_pct:.1f}% of each other - a classic bearish reversal "
            f"silhouette. Not confirmed (no neckline-break or volume check)."
        ),
    )


def detect_cup_and_handle(closes: list[float]) -> PatternHint | None:
    """A rounded U-shaped recovery (cup) back to near the prior high,
    followed by a shallow pullback (handle) - a classic bullish continuation
    silhouette."""
    if len(closes) < CUP_AND_HANDLE_LOOKBACK:
        return None
    window = np.array(closes[-CUP_AND_HANDLE_LOOKBACK:])

    troughs, _ = find_peaks(-window, distance=10, prominence=np.std(window) * 0.5)
    if len(troughs) == 0:
        return None

    cup_bottom_idx = troughs[np.argmin(window[troughs])]
    if cup_bottom_idx < 5 or cup_bottom_idx > len(window) - 10:
        return None  # not enough room on both sides for a rim + handle

    left_rim = window[:cup_bottom_idx].max()
    right_segment = window[cup_bottom_idx:]
    right_rim_idx = cup_bottom_idx + int(np.argmax(right_segment))
    right_rim = window[right_rim_idx]

    rim_diff_pct = abs(left_rim - right_rim) / ((left_rim + right_rim) / 2) * 100
    if rim_diff_pct > CUP_AND_HANDLE_RIM_TOLERANCE_PCT:
        return None
    if right_rim_idx >= len(window) - 2:
        return None  # no room left for a handle after the right rim

    handle_low = window[right_rim_idx:].min()
    if handle_low <= window[cup_bottom_idx]:
        return None  # pulled back below the cup itself - not a shallow handle

    return PatternHint(
        name="Cup and Handle",
        description=(
            f"A rounded recovery back to within {rim_diff_pct:.1f}% of the prior "
            f"high, followed by a shallow pullback - a classic bullish continuation "
            f"silhouette. Heuristic only, not confirmed."
        ),
    )


def detect_patterns(closes: list[float]) -> list[PatternHint]:
    hints = []
    hs = detect_head_and_shoulders(closes)
    if hs:
        hints.append(hs)
    ch = detect_cup_and_handle(closes)
    if ch:
        hints.append(ch)
    return hints
