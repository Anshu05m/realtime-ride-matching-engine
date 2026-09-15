"""Candidate-discovery: given a rider's pickup point, find nearby available
drivers cheaply using H3 ring expansion, then rank them by real distance.

This is the "Determine rider's H3 cell -> Search nearby H3 cells -> Retrieve
available driver candidates -> Calculate actual geographic distance -> Rank
candidates" portion of the matching flow described in CLAUDE.md section 7.
No locking or assignment happens here — see matcher.py for that.
"""

from sqlalchemy.orm import Session

from app.config import settings
from app.matching.geo import cells_within_ring, latlng_to_h3
from app.matching.ranking import rank_candidates
from app.models.driver import Driver
from app.storage.repositories.driver_repository import DriverRepository


def find_ranked_candidates(
    db: Session,
    rider_lat: float,
    rider_lng: float,
    *,
    target_candidates: int = settings.match_target_candidates,
    max_ring: int = settings.match_max_ring,
) -> list[tuple[Driver, float]]:
    """Expands H3 rings outward from the rider's pickup cell, stopping once
    `target_candidates` available drivers have been found or `max_ring` is
    exhausted, then returns every candidate found ranked by real distance.

    Each ring is queried for *new* cells only (see geo.cells_within_ring), so a
    driver already found in an earlier ring is never queried or counted twice.
    """
    rider_cell = latlng_to_h3(rider_lat, rider_lng, settings.h3_resolution)
    driver_repo = DriverRepository(db)

    candidates: dict = {}
    for k in range(0, max_ring + 1):
        ring_cells = cells_within_ring(rider_cell, k)
        for driver in driver_repo.list_available_in_cells(ring_cells):
            candidates[driver.id] = driver
        if len(candidates) >= target_candidates:
            break

    return rank_candidates(rider_lat, rider_lng, list(candidates.values()))
