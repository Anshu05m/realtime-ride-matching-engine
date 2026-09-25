"""Real-DB tests for the Slice 2 matching algorithm (no locking yet).

Requires `docker compose up -d postgres redis` running first.
"""

import h3

from app.config import settings
from app.matching.geo import cells_within_ring, latlng_to_h3
from app.matching.matcher import NoAvailableDriverError, match_ride
from app.models.driver import DriverStatus
from app.models.ride import InvalidRideStateError, RideStatus
from app.services.ride_service import cancel_ride
from app.storage.repositories.driver_repository import DriverRepository
from app.storage.repositories.ride_repository import RideRepository
from app.storage.repositories.rider_repository import RiderRepository

RIDER_LAT, RIDER_LNG = 37.7749, -122.4194


def _cell_center(ring: int, index: int = 0) -> tuple[float, float, str]:
    rider_cell = latlng_to_h3(RIDER_LAT, RIDER_LNG, settings.h3_resolution)
    cells = sorted(cells_within_ring(rider_cell, ring))
    cell = cells[index]
    lat, lng = h3.cell_to_latlng(cell)
    return lat, lng, cell


def _requested_ride(db_session):
    rider = RiderRepository(db_session).create(pickup_lat=RIDER_LAT, pickup_lng=RIDER_LNG)
    return RideRepository(db_session).create(
        rider_id=rider.id, pickup_lat=RIDER_LAT, pickup_lng=RIDER_LNG
    )


def test_match_ride_assigns_nearest_available_driver(db_session):
    near_lat, near_lng, near_cell = _cell_center(ring=0)
    far_lat, far_lng, far_cell = _cell_center(ring=2)

    driver_repo = DriverRepository(db_session)
    near = driver_repo.create(
        current_lat=near_lat, current_lng=near_lng, h3_index=near_cell, zone_id="zone-a"
    )
    driver_repo.create(current_lat=far_lat, current_lng=far_lng, h3_index=far_cell, zone_id="zone-a")

    ride = _requested_ride(db_session)
    matched = match_ride(db_session, ride)

    assert matched.driver_id == near.id
    assert matched.status == RideStatus.MATCHED
    assert matched.matched_at is not None
    assert driver_repo.get_by_id(near.id).status == DriverStatus.BUSY


def test_match_ride_skips_busy_drivers(db_session):
    near_lat, near_lng, near_cell = _cell_center(ring=0)
    far_lat, far_lng, far_cell = _cell_center(ring=1)

    driver_repo = DriverRepository(db_session)
    driver_repo.create(
        current_lat=near_lat,
        current_lng=near_lng,
        h3_index=near_cell,
        zone_id="zone-a",
        status=DriverStatus.BUSY,
    )
    available = driver_repo.create(
        current_lat=far_lat, current_lng=far_lng, h3_index=far_cell, zone_id="zone-a"
    )

    ride = _requested_ride(db_session)
    matched = match_ride(db_session, ride)

    assert matched.driver_id == available.id


def test_match_ride_raises_and_leaves_state_untouched_when_no_driver_available(db_session):
    ride = _requested_ride(db_session)

    try:
        match_ride(db_session, ride)
        raised = False
    except NoAvailableDriverError:
        raised = True

    assert raised
    assert ride.status == RideStatus.REQUESTED
    assert ride.driver_id is None


def test_match_ride_rejects_ride_not_in_requested_status(db_session):
    ride = _requested_ride(db_session)
    RideRepository(db_session).update_status(ride.id, status=RideStatus.CANCELLED)

    try:
        match_ride(db_session, ride)
        raised = False
    except ValueError:
        raised = True

    assert raised


def test_match_ride_falls_through_when_top_candidate_goes_busy_before_assignment(
    db_session, monkeypatch
):
    """This is the one race the Slice 2 re-check step DOES catch: a candidate
    going BUSY between discovery and assignment. It is NOT a test of real
    concurrency safety -- see app/matching/matcher.py's module docstring for what
    remains unsafe until Slice 3's locking lands."""
    near_lat, near_lng, near_cell = _cell_center(ring=0)
    far_lat, far_lng, far_cell = _cell_center(ring=1)

    driver_repo = DriverRepository(db_session)
    top_candidate = driver_repo.create(
        current_lat=near_lat, current_lng=near_lng, h3_index=near_cell, zone_id="zone-a"
    )
    fallback = driver_repo.create(
        current_lat=far_lat, current_lng=far_lng, h3_index=far_cell, zone_id="zone-a"
    )

    ride = _requested_ride(db_session)

    import app.matching.matcher as matcher_module

    original_find = matcher_module.find_ranked_candidates

    def find_then_steal_top_candidate(db, rider_lat, rider_lng, **kwargs):
        ranked = original_find(db, rider_lat, rider_lng, **kwargs)
        driver_repo.update_status(top_candidate.id, status=DriverStatus.BUSY)
        return ranked

    monkeypatch.setattr(matcher_module, "find_ranked_candidates", find_then_steal_top_candidate)

    matched = match_ride(db_session, ride)

    assert matched.driver_id == fallback.id


def test_match_ride_does_not_overwrite_a_concurrently_cancelled_ride(db_session, monkeypatch):
    """The ride-side counterpart to the driver-steal test above -- and the
    specific race the user asked to have verified before approving Slice 6's
    design: if the ride itself is cancelled (by a concurrent cancel_ride) in
    the gap between candidate discovery and match_ride's own conditional
    UPDATE, match_ride must not silently overwrite that cancellation back to
    MATCHED. See the module docstring's point 4 and INTERVIEW_PREP.md's
    Slice 6 section for the full writeup."""
    near_lat, near_lng, near_cell = _cell_center(ring=0)

    driver_repo = DriverRepository(db_session)
    candidate = driver_repo.create(
        current_lat=near_lat, current_lng=near_lng, h3_index=near_cell, zone_id="zone-a"
    )

    ride = _requested_ride(db_session)

    import app.matching.matcher as matcher_module

    original_find = matcher_module.find_ranked_candidates

    def find_then_cancel_the_ride(db, rider_lat, rider_lng, **kwargs):
        ranked = original_find(db, rider_lat, rider_lng, **kwargs)
        cancel_ride(db, ride.id)
        return ranked

    monkeypatch.setattr(matcher_module, "find_ranked_candidates", find_then_cancel_the_ride)

    try:
        match_ride(db_session, ride)
        raised = False
    except InvalidRideStateError:
        raised = True

    assert raised

    db_session.refresh(ride)
    assert ride.status == RideStatus.CANCELLED  # not silently overwritten to MATCHED
    assert driver_repo.get_by_id(candidate.id, fresh=True).status == DriverStatus.AVAILABLE
