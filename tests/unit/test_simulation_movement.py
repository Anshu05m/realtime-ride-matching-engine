"""Slice 7: pure-function tests for the driver-movement random walk."""

import random

from app.matching.geo import haversine_km
from app.simulation.generators import Point
from app.simulation.movement import step


def test_step_respects_max_displacement():
    rng = random.Random(1)
    start = Point(lat=37.7749, lng=-122.4194)
    speed_kmh = 30.0
    interval_seconds = 2.0
    max_distance_km = speed_kmh * (interval_seconds / 3600.0)
    for _ in range(100):
        new_position = step(
            start,
            area_center=start,
            area_radius_km=50.0,  # generous, so clamping never kicks in here
            speed_kmh=speed_kmh,
            interval_seconds=interval_seconds,
            rng=rng,
        )
        assert haversine_km(start.lat, start.lng, new_position.lat, new_position.lng) <= max_distance_km + 1e-9


def test_step_clamps_to_area_boundary():
    rng = random.Random(2)
    area_center = Point(lat=0.0, lng=0.0)
    edge_position = Point(lat=0.045, lng=0.0)  # ~5km north of center
    new_position = step(
        edge_position,
        area_center=area_center,
        area_radius_km=5.0,
        speed_kmh=100.0,
        interval_seconds=3600.0,  # a deliberately huge step to force clamping
        rng=rng,
    )
    assert haversine_km(area_center.lat, area_center.lng, new_position.lat, new_position.lng) <= 5.0 + 1e-6


def test_step_is_deterministic_with_seeded_rng():
    start = Point(lat=10.0, lng=10.0)
    a = step(
        start, area_center=start, area_radius_km=50.0, speed_kmh=20.0, interval_seconds=2.0, rng=random.Random(7)
    )
    b = step(
        start, area_center=start, area_radius_km=50.0, speed_kmh=20.0, interval_seconds=2.0, rng=random.Random(7)
    )
    assert a == b


def test_step_within_area_is_a_noop_for_clamping():
    rng = random.Random(3)
    area_center = Point(lat=0.0, lng=0.0)
    start = Point(lat=0.0, lng=0.0)
    new_position = step(
        start,
        area_center=area_center,
        area_radius_km=50.0,
        speed_kmh=5.0,
        interval_seconds=1.0,
        rng=rng,
    )
    # A tiny step well within a huge area should not be pulled toward center.
    assert haversine_km(area_center.lat, area_center.lng, new_position.lat, new_position.lng) > 0
