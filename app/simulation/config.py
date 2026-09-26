"""Slice 7: configuration for the standalone simulation engine.

Deliberately NOT part of app.config.Settings -- Settings is process-wide
server config, loaded once and shared by every request; these parameters vary
per simulation run/experiment and the running server never reads them.
Configured via CLI flags (see __main__.py), not environment variables, to
keep that boundary honest.
"""

from __future__ import annotations

from dataclasses import dataclass

import h3

from app.config import settings
from app.matching.geo import cells_within_ring, haversine_km, latlng_to_h3
from app.simulation.generators import Point

# Matches the coordinates already used throughout the concurrency test suite
# (tests/concurrency/test_driver_assignment.py), so a simulation run and the
# existing tests are talking about the same part of the world.
DEFAULT_AREA_CENTER_LAT = 37.7749
DEFAULT_AREA_CENTER_LNG = -122.4194


def default_hotspot_radius_km(
    center_lat: float = DEFAULT_AREA_CENTER_LAT,
    center_lng: float = DEFAULT_AREA_CENTER_LNG,
) -> float:
    """Derives a hotspot radius from the actual matching config instead of a
    guessed constant.

    settings.h3_resolution/settings.match_max_ring define
    find_ranked_candidates's real candidate-search radius -- a hotspot
    spatially larger than that would scatter riders across candidate sets
    that never overlap, producing apparent proximity with no real lock
    contention. Computed via H3's actual ring geometry (the true distance to
    the farthest cell in the outer searched ring) so this stays correct if
    h3_resolution/match_max_ring ever change, rather than a hardcoded
    edge-length constant.
    """
    center_cell = latlng_to_h3(center_lat, center_lng, settings.h3_resolution)
    outer_ring = cells_within_ring(center_cell, settings.match_max_ring)
    if not outer_ring:
        return 1.0
    distances = [haversine_km(center_lat, center_lng, *h3.cell_to_latlng(cell)) for cell in outer_ring]
    return round(max(distances), 2)


@dataclass
class SimulationConfig:
    api_url: str = "http://localhost:8000"

    num_drivers: int = 50
    num_riders: int = 500

    area_center_lat: float = DEFAULT_AREA_CENTER_LAT
    area_center_lng: float = DEFAULT_AREA_CENTER_LNG
    area_radius_km: float = 5.0

    # Fraction of ride requests biased into the (small) hotspot rather than
    # uniform over the (larger) area -- see generators.random_point_biased.
    hotspot_fraction: float = 0.7
    hotspot_center_lat: float | None = None  # None -> defaults to area center
    hotspot_center_lng: float | None = None
    hotspot_radius_km: float | None = None  # None -> default_hotspot_radius_km()

    # Paired: a burst of `burst_size` concurrent requests fires every
    # burst_size / request_rate seconds (see tick_interval_seconds).
    request_rate: float = 5.0
    burst_size: int = 5
    duration_seconds: float = 60.0

    driver_speed_kmh: float = 20.0
    movement_interval_seconds: float = 2.0

    # How long a matched ride stays active before the lifecycle loop
    # completes (or, per cancel_fraction, cancels) it -- tune this relative
    # to request_rate/burst_size: too short and drivers barely stay busy long
    # enough to be contested; too long and the driver pool depletes faster
    # than it's freed.
    ride_duration_min_seconds: float = 5.0
    ride_duration_max_seconds: float = 15.0
    cancel_fraction: float = 0.1

    seed: int | None = None

    def area_center(self) -> Point:
        return Point(lat=self.area_center_lat, lng=self.area_center_lng)

    def resolved_hotspot_center(self) -> Point:
        return Point(
            lat=self.hotspot_center_lat if self.hotspot_center_lat is not None else self.area_center_lat,
            lng=self.hotspot_center_lng if self.hotspot_center_lng is not None else self.area_center_lng,
        )

    def resolved_hotspot_radius_km(self) -> float:
        if self.hotspot_radius_km is not None:
            return self.hotspot_radius_km
        return default_hotspot_radius_km(self.area_center_lat, self.area_center_lng)

    @property
    def tick_interval_seconds(self) -> float:
        """Seconds between bursts, derived from request_rate/burst_size --
        e.g. rate=10, burst=5 -> a burst of 5 concurrent requests every 0.5s."""
        if self.request_rate <= 0:
            return float("inf")
        return self.burst_size / self.request_rate
