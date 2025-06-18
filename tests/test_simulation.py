from co2cooling import calculate_peltier_efficiency, calculate_fan_multiplier


def test_peltier_efficiency_decreases_with_temp_diff():
    base = calculate_peltier_efficiency(50, 50)
    lower = calculate_peltier_efficiency(50, 80)
    assert lower < base


def test_fan_multiplier_post_purge_boost():
    base = calculate_fan_multiplier(50)
    boosted = calculate_fan_multiplier(50, is_post_purge=True, purge_timer=90)
    assert boosted > base
