"""Slice 7: bounded random-walk driver movement.

A separate module from generators.py (one-shot point generation) because
"where does a driver start" and "how does a driver move on each tick" are
different enough responsibilities to keep apart -- the same granularity this
codebase already uses for app/matching/candidate_search.py vs
app/matching/ranking.py.
"""

from __future__ import annotations

import math
import random

from app.simulation.generators import KM_PER_DEGREE_LAT, Point, km_per_degree_lng


def step(
    position: Point,
    *,
    area_center: Point,
    area_radius_km: float,
    speed_kmh: float,
    interval_seconds: float,
    rng: random.Random,
) -> Point:
    """Moves a driver a bounded random distance/direction, then clamps the
    result back onto the area boundary if the step would carry it outside --
    reflecting inward rather than letting drivers wander permanently off the
    simulated map, so the area/hotspot configuration stays meaningful for the
    whole run instead of drivers slowly draining out of it.
    """
    max_distance_km = speed_kmh * (interval_seconds / 3600.0)
    distance_km = rng.uniform(0, max_distance_km)
    angle = rng.uniform(0, 2 * math.pi)

    lat_offset = (distance_km * math.cos(angle)) / KM_PER_DEGREE_LAT
    km_per_lng = km_per_degree_lng(position.lat)
    lng_offset = (distance_km * math.sin(angle)) / km_per_lng if km_per_lng else 0.0

    moved = Point(lat=position.lat + lat_offset, lng=position.lng + lng_offset)
    return _clamp_to_area(moved, area_center, area_radius_km)


def _clamp_to_area(position: Point, area_center: Point, area_radius_km: float) -> Point:
    """If position has drifted outside the area radius, reflects it back onto
    the boundary along the same bearing from center."""
    km_per_lng = km_per_degree_lng(area_center.lat)
    dx_km = (position.lng - area_center.lng) * km_per_lng
    dy_km = (position.lat - area_center.lat) * KM_PER_DEGREE_LAT
    distance_from_center = math.hypot(dx_km, dy_km)
    if distance_from_center <= area_radius_km or distance_from_center == 0:
        return position
    scale = area_radius_km / distance_from_center
    return Point(
        lat=area_center.lat + (dy_km * scale) / KM_PER_DEGREE_LAT,
        lng=area_center.lng + (dx_km * scale) / km_per_lng if km_per_lng else area_center.lng,
    )
