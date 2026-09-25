"""Pure formula tests -- no database. surge_multiplier is a plain function of
(demand, supply); see app/pricing/surge.py for the formula itself and the
reasoning for choosing a continuous, capped formula over a step table.
"""

from app.config import settings
from app.pricing.surge import surge_multiplier


def test_zero_demand_gives_the_floor_multiplier():
    assert surge_multiplier(demand=0, supply=10) == 1.0


def test_zero_supply_does_not_divide_by_zero():
    # max(supply, 1) floors the denominator -- this must return a large but
    # finite (and clamped) number, not raise.
    result = surge_multiplier(demand=5, supply=0)
    assert result == settings.surge_max_multiplier


def test_multiplier_increases_with_demand():
    low = surge_multiplier(demand=1, supply=10)
    high = surge_multiplier(demand=8, supply=10)
    assert high > low


def test_multiplier_never_drops_below_one():
    assert surge_multiplier(demand=0, supply=1000) >= 1.0


def test_multiplier_is_clamped_at_the_configured_maximum():
    huge_ratio = surge_multiplier(demand=1_000_000, supply=1)
    assert huge_ratio == settings.surge_max_multiplier


def test_multiplier_is_deterministic():
    # CLAUDE.md requires the pricing system to actually be deterministic,
    # not just claimed to be -- so check it, not just assert once.
    results = {surge_multiplier(demand=7, supply=3) for _ in range(50)}
    assert len(results) == 1


def test_equal_demand_and_supply_is_a_known_reference_point():
    # ratio == 1.0, so multiplier == 1.0 + sensitivity, by construction.
    expected = 1.0 + settings.surge_sensitivity * 1.0
    assert surge_multiplier(demand=5, supply=5) == min(expected, settings.surge_max_multiplier)
