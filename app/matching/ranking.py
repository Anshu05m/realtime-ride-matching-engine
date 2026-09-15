"""Pure, DB-free ranking of candidate drivers by real geographic distance.

Kept separate from candidate_search.py (which talks to the database) so the
ranking logic itself is unit-testable without a live Postgres connection — see
tests/unit/test_ranking.py.
"""

from app.matching.geo import haversine_km
from app.models.driver import Driver


def rank_candidates(
    rider_lat: float, rider_lng: float, drivers: list[Driver]
) -> list[tuple[Driver, float]]:
    """Returns (driver, distance_km) pairs sorted by ascending distance from the
    rider's pickup point."""
    ranked = [
        (driver, haversine_km(rider_lat, rider_lng, driver.current_lat, driver.current_lng))
        for driver in drivers
    ]
    ranked.sort(key=lambda pair: pair[1])
    return ranked
