"""Slice 7: pure, DB/network-free generation of synthetic points.

Kept separate from movement.py (one-shot generation vs. a per-tick step
function) and from client.py (no I/O here at all) -- mirrors this codebase's
existing convention of splitting pure logic from things that talk to a
database/API (see app/matching/ranking.py vs app/matching/candidate_search.py).
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass

# Local flat-earth approximation: fine at city scale (this project's whole
# simulated area is a handful of km across), not valid near the poles, which
# this project's simulated area never is.
KM_PER_DEGREE_LAT = 111.32


@dataclass(frozen=True)
class Point:
    lat: float
    lng: float


def km_per_degree_lng(lat: float) -> float:
    """Local flat-earth approximation of km-per-degree-longitude at a given
    latitude (shrinks toward the poles; fine at this project's city scale)."""
    return KM_PER_DEGREE_LAT * math.cos(math.radians(lat))


def random_point_in_radius(center: Point, radius_km: float, rng: random.Random) -> Point:
    """Uniformly random point within radius_km of center.

    Uses sqrt(u) for the radial draw (not a plain uniform radius), which is
    required for points to be uniform over the *area* of the circle rather
    than clustering near the center -- a plain `rng.uniform(0, radius_km)`
    radius would bias points toward the middle.
    """
    if radius_km <= 0:
        return center
    angle = rng.uniform(0, 2 * math.pi)
    distance_km = radius_km * math.sqrt(rng.uniform(0, 1))
    lat_offset = (distance_km * math.cos(angle)) / KM_PER_DEGREE_LAT
    km_per_lng = km_per_degree_lng(center.lat)
    lng_offset = (distance_km * math.sin(angle)) / km_per_lng if km_per_lng else 0.0
    return Point(lat=center.lat + lat_offset, lng=center.lng + lng_offset)


def random_point_biased(
    *,
    area_center: Point,
    area_radius_km: float,
    hotspot_center: Point,
    hotspot_radius_km: float,
    hotspot_fraction: float,
    rng: random.Random,
) -> Point:
    """With probability hotspot_fraction, returns a point concentrated in the
    (small) hotspot; otherwise a point uniform over the whole (larger) area.

    This asymmetry -- concentrated demand here, uniformly-scattered supply in
    runner.py's driver placement -- is what actually produces scarcity for a
    specific small set of drivers, per CLAUDE.md section 12's example flow
    ("many riders -> same zone -> small pool of drivers"), rather than a
    generic "make it busier" load increase.
    """
    if rng.random() < hotspot_fraction:
        return random_point_in_radius(hotspot_center, hotspot_radius_km, rng)
    return random_point_in_radius(area_center, area_radius_km, rng)


def generate_points(center: Point, radius_km: float, count: int, rng: random.Random) -> list[Point]:
    """Uniform placement across the whole area -- used for driver placement,
    which is deliberately NOT hotspot-biased (see runner.py)."""
    return [random_point_in_radius(center, radius_km, rng) for _ in range(count)]
