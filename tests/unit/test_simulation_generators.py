"""Slice 7: pure-function tests for synthetic point generation. No DB/network
needed -- these are exactly the kind of pure logic this codebase's unit tests
already cover (compare tests/unit/test_geo.py, tests/unit/test_ranking.py)."""

import random

from app.matching.geo import haversine_km
from app.simulation.generators import (
    Point,
    generate_points,
    random_point_biased,
    random_point_in_radius,
)


def _distance_km(a: Point, b: Point) -> float:
    return haversine_km(a.lat, a.lng, b.lat, b.lng)


def test_random_point_in_radius_stays_within_bound():
    rng = random.Random(1)
    center = Point(lat=37.7749, lng=-122.4194)
    for _ in range(200):
        point = random_point_in_radius(center, 2.0, rng)
        assert _distance_km(center, point) <= 2.0 + 1e-6


def test_random_point_in_radius_zero_radius_returns_center():
    rng = random.Random(2)
    center = Point(lat=10.0, lng=20.0)
    assert random_point_in_radius(center, 0.0, rng) == center


def test_random_point_in_radius_is_deterministic_with_seeded_rng():
    center = Point(lat=1.0, lng=1.0)
    a = random_point_in_radius(center, 5.0, random.Random(42))
    b = random_point_in_radius(center, 5.0, random.Random(42))
    assert a == b


def test_random_point_in_radius_is_not_all_clustered_near_the_center():
    # Regression guard for the sqrt(u) radial draw: a plain uniform radius
    # would bias points toward the center instead of being area-uniform.
    rng = random.Random(3)
    center = Point(lat=0.0, lng=0.0)
    radius_km = 10.0
    inner_half_count = sum(
        1 for _ in range(500) if _distance_km(center, random_point_in_radius(center, radius_km, rng)) < radius_km / 2
    )
    # Area-uniform over a disk -> ~25% of points fall within half the radius
    # (area scales with r^2). Allow generous slack for randomness.
    assert inner_half_count < 350


def test_random_point_biased_always_lands_in_hotspot_when_fraction_is_one():
    rng = random.Random(4)
    area_center = Point(lat=37.7749, lng=-122.4194)
    hotspot_center = Point(lat=37.78, lng=-122.41)
    for _ in range(50):
        point = random_point_biased(
            area_center=area_center,
            area_radius_km=10.0,
            hotspot_center=hotspot_center,
            hotspot_radius_km=0.5,
            hotspot_fraction=1.0,
            rng=rng,
        )
        assert _distance_km(hotspot_center, point) <= 0.5 + 1e-6


def test_random_point_biased_can_land_outside_hotspot_when_fraction_is_zero():
    rng = random.Random(5)
    area_center = Point(lat=37.7749, lng=-122.4194)
    hotspot_center = Point(lat=37.9, lng=-122.6)  # far away from area_center
    outside_hotspot_seen = False
    for _ in range(50):
        point = random_point_biased(
            area_center=area_center,
            area_radius_km=10.0,
            hotspot_center=hotspot_center,
            hotspot_radius_km=0.5,
            hotspot_fraction=0.0,
            rng=rng,
        )
        if _distance_km(hotspot_center, point) > 0.5:
            outside_hotspot_seen = True
    assert outside_hotspot_seen


def test_generate_points_returns_requested_count_within_radius():
    rng = random.Random(6)
    center = Point(lat=0.0, lng=0.0)
    points = generate_points(center, 3.0, 25, rng)
    assert len(points) == 25
    assert all(_distance_km(center, p) <= 3.0 + 1e-6 for p in points)
