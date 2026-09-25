from marketpulse.sector_flow import (
    AVG_VOLUME_WINDOW,
    VOLUME_RATIO_THRESHOLD,
    classify_flow,
    compute_daily_flows,
)


def test_classify_flow_needs_both_inputs():
    assert classify_flow(None, 1.0) is None
    assert classify_flow(2.0, None) is None


def test_classify_flow_high_ratio_up_day_is_accumulation():
    assert classify_flow(VOLUME_RATIO_THRESHOLD + 0.1, 1.0) == "Accumulation"


def test_classify_flow_high_ratio_down_day_is_distribution():
    assert classify_flow(VOLUME_RATIO_THRESHOLD + 0.1, -1.0) == "Distribution"


def test_classify_flow_low_ratio_is_neutral_regardless_of_direction():
    assert classify_flow(1.0, 5.0) == "Neutral"
    assert classify_flow(1.0, -5.0) == "Neutral"


def test_compute_daily_flows_first_days_have_no_avg_until_window_fills():
    n = AVG_VOLUME_WINDOW + 5
    closes = [100.0 + i for i in range(n)]
    volumes = [1000] * n

    flows = compute_daily_flows(closes, volumes)

    assert len(flows) == n
    assert flows[AVG_VOLUME_WINDOW - 1].avg_volume_20d is None  # only 19 prior days exist
    assert flows[AVG_VOLUME_WINDOW].avg_volume_20d == 1000  # 20 prior days now exist
    assert flows[0].flow_status is None  # no prior day to compare price to


def test_compute_daily_flows_spike_on_up_day_flags_accumulation():
    n = AVG_VOLUME_WINDOW + 1
    closes = [100.0] * n
    volumes = [1000] * (n - 1) + [3000]  # big volume spike on the last day
    closes[-1] = 105.0  # and price rose

    flows = compute_daily_flows(closes, volumes)

    assert flows[-1].volume_ratio == 3.0
    assert flows[-1].flow_status == "Accumulation"


def test_compute_daily_flows_rejects_mismatched_lengths():
    import pytest

    with pytest.raises(ValueError):
        compute_daily_flows([1.0, 2.0], [100])
