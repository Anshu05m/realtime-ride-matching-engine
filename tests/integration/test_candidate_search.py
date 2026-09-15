"""Real-DB tests for H3 ring expansion + candidate retrieval.

Requires `docker compose up -d postgres redis` running first.
"""

import h3

from app.config import settings
from app.matching.candidate_search import find_ranked_candidates
from app.matching.geo import cells_within_ring, latlng_to_h3
from app.models.driver import DriverStatus
from app.storage.repositories.driver_repository import DriverRepository

RIDER_LAT, RIDER_LNG = 37.7749, -122.4194


def _cell_center(ring: int, index: int = 0) -> tuple[float, float, str]:
    """Returns the exact center lat/lng (and cell id) of a cell `ring` rings away
    from the rider's cell, so a driver placed there is guaranteed to land in that
    ring — no guessing coordinate offsets."""
    rider_cell = latlng_to_h3(RIDER_LAT, RIDER_LNG, settings.h3_resolution)
    cells = sorted(cells_within_ring(rider_cell, ring))
    cell = cells[index]
    lat, lng = h3.cell_to_latlng(cell)
    return lat, lng, cell


def test_finds_driver_only_present_at_a_farther_ring(db_session):
    # Nothing at ring 0/1 -- the driver only shows up once expansion reaches ring 2,
    # so this actually exercises the expansion loop rather than trivially passing.
    lat, lng, cell = _cell_center(ring=2)
    driver = DriverRepository(db_session).create(current_lat=lat, current_lng=lng, h3_index=cell)

    ranked = find_ranked_candidates(
        db_session, RIDER_LAT, RIDER_LNG, target_candidates=1, max_ring=3
    )

    assert [d.id for d, _ in ranked] == [driver.id]


def test_stops_before_max_ring_if_nothing_found(db_session):
    ranked = find_ranked_candidates(
        db_session, RIDER_LAT, RIDER_LNG, target_candidates=1, max_ring=1
    )
    assert ranked == []


def test_busy_driver_in_near_cell_is_excluded(db_session):
    # Proves the AVAILABLE filter, not just the H3 cell filter.
    lat, lng, cell = _cell_center(ring=0)
    DriverRepository(db_session).create(
        current_lat=lat, current_lng=lng, h3_index=cell, status=DriverStatus.BUSY
    )

    ranked = find_ranked_candidates(
        db_session, RIDER_LAT, RIDER_LNG, target_candidates=1, max_ring=1
    )

    assert ranked == []


def test_no_duplicate_drivers_across_ring_iterations(db_session):
    # Regression test for cells_within_ring's disk-diffing: if new cells overlapped
    # already-seen cells, this driver would be counted (and returned) more than once.
    lat, lng, cell = _cell_center(ring=0)
    driver = DriverRepository(db_session).create(current_lat=lat, current_lng=lng, h3_index=cell)

    ranked = find_ranked_candidates(
        db_session, RIDER_LAT, RIDER_LNG, target_candidates=5, max_ring=3
    )

    ids = [d.id for d, _ in ranked]
    assert ids.count(driver.id) == 1


def test_stops_expanding_once_target_candidate_count_reached(db_session):
    driver_repo = DriverRepository(db_session)
    ring0_lat, ring0_lng, ring0_cell = _cell_center(ring=0)
    driver_repo.create(current_lat=ring0_lat, current_lng=ring0_lng, h3_index=ring0_cell)

    ranked = find_ranked_candidates(
        db_session, RIDER_LAT, RIDER_LNG, target_candidates=1, max_ring=3
    )

    assert len(ranked) == 1
