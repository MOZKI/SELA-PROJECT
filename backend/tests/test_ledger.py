import pytest
from app.ledger import (Sample, integrate_step, apply_energy, apply_topup, is_online, is_low)


def test_first_sample_no_energy():
    s = integrate_step(None, Sample(100, 10))
    assert s.delta_wh == 0 and not s.gap and s.reason == "first"


def test_trapezoid_10w_5s():
    s = integrate_step(Sample(100, 10), Sample(105, 10))
    assert s.delta_wh == pytest.approx(10 * 5 / 3600)
    assert not s.gap


def test_trapezoid_changing_power():
    s = integrate_step(Sample(0, 10), Sample(10, 30))
    assert s.delta_wh == pytest.approx(20 * 10 / 3600)


def test_gap_over_15s_not_integrated():
    s = integrate_step(Sample(0, 10), Sample(20, 10))
    assert s.delta_wh == 0 and s.gap and s.reason == "gap"


def test_gap_exactly_15s_is_integrated():
    s = integrate_step(Sample(0, 10), Sample(15, 10))
    assert s.delta_wh > 0 and not s.gap


def test_sensor_failure_is_not_zero_watt():
    s = integrate_step(Sample(0, 10), Sample(5, None))
    assert s.delta_wh == 0 and s.gap and s.reason == "sensor"
    s = integrate_step(Sample(0, None), Sample(5, 10))
    assert s.delta_wh == 0 and s.gap


def test_out_of_order_ignored():
    s = integrate_step(Sample(10, 10), Sample(10, 10))
    assert s.delta_wh == 0 and s.gap and s.reason == "out_of_order"
    s = integrate_step(Sample(10, 10), Sample(5, 10))
    assert s.reason == "out_of_order"


def test_apply_energy_normal():
    assert apply_energy(5.0, 1.0) == (4.0, 0.0)


def test_apply_energy_clamps_at_zero_and_records_overshoot():
    bal, over = apply_energy(0.01, 0.03)
    assert bal == 0.0 and over == pytest.approx(0.02)


def test_topup():
    assert apply_topup(0.0, 5.0) == 5.0
    with pytest.raises(ValueError):
        apply_topup(1.0, 0)


def test_online_and_low():
    assert is_online(10) and is_online(15) and not is_online(15.1) and not is_online(None)
    assert is_low(1.0, 5.0) and not is_low(1.1, 5.0) and not is_low(0.0, 5.0)
