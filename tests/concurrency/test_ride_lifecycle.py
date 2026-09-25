"""Slice 6: genuinely concurrent match_ride and cancel_ride racing on the
same REQUESTED ride, proving the shared atomic-conditional-UPDATE mechanism
(RideRepository.transition_status) keeps the outcome coherent under real
cross-session concurrency -- not just the monkeypatch-simulated race in
tests/integration/test_matcher.py.

Because cancel_ride's valid source statuses (ACTIVE_RIDE_STATUSES =
REQUESTED/MATCHED/IN_PROGRESS) are a strict superset of what match_ride can
ever set a ride to (MATCHED), cancel structurally always wins a two-party
race against match alone, however the two interleave:
  - If cancel's conditional UPDATE reaches the row while it's still
    REQUESTED, it transitions REQUESTED -> CANCELLED directly, and match
    then fails -- either its own top-of-function precondition check catches
    it on a fresh read (raising ValueError), or, if match had already passed
    that check before cancel committed, its later conditional UPDATE
    (from_statuses=[REQUESTED]) fails instead (raising
    InvalidRideStateError). Same race, caught at whichever point it's
    detected.
  - If match's conditional UPDATE commits first (REQUESTED -> MATCHED), that
    is not final: cancel's UPDATE (MATCHED is a valid cancel-source) then
    succeeds too, transitioning MATCHED -> CANCELLED and freeing the driver.

So the deterministic invariant across every trial, regardless of
interleaving, is: the ride always ends CANCELLED and the driver always ends
AVAILABLE -- never a driver stranded BUSY with no active ride attached to
it. That second branch is exactly the scenario that would have caught the
"refresh before checking driver_id" bug found during Slice 6's design (see
INTERVIEW_PREP.md): if cancel_ride used a stale, pre-transition driver_id
instead of refreshing after its own conditional UPDATE, it would fail to
free a driver that match_ride assigned in the interleaving window.

Requires `docker compose up -d postgres redis` running first.
"""

import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.config import settings
from app.matching.geo import latlng_to_h3
from app.matching.matcher import NoAvailableDriverError, match_ride
from app.models.driver import DriverStatus
from app.models.ride import InvalidRideStateError, RideStatus
from app.services.ride_service import cancel_ride
from app.storage.repositories.driver_repository import DriverRepository
from app.storage.repositories.ride_repository import RideRepository
from app.storage.repositories.rider_repository import RiderRepository

LAT, LNG = 37.7749, -122.4194
H3_INDEX = latlng_to_h3(LAT, LNG, settings.h3_resolution)
ZONE_ID = "zone-a"


def _make_driver(session):
    return DriverRepository(session).create(
        current_lat=LAT, current_lng=LNG, h3_index=H3_INDEX, zone_id=ZONE_ID
    )


def _make_requested_ride(session):
    rider = RiderRepository(session).create(pickup_lat=LAT, pickup_lng=LNG)
    return RideRepository(session).create(rider_id=rider.id, pickup_lat=LAT, pickup_lng=LNG)


@pytest.mark.parametrize("trial", range(20))
def test_concurrent_match_and_cancel_never_strand_a_busy_driver(
    committing_session_factory, trial
):
    setup = committing_session_factory()
    driver = _make_driver(setup)
    ride = _make_requested_ride(setup)
    driver_id, ride_id = driver.id, ride.id
    setup.commit()
    setup.close()

    barrier = threading.Barrier(2)

    def do_match():
        session = committing_session_factory()
        try:
            barrier.wait()
            ride_obj = RideRepository(session).get_by_id(ride_id)
            try:
                match_ride(session, ride_obj)
            except (NoAvailableDriverError, InvalidRideStateError, ValueError):
                # ValueError: match_ride's own fresh fetch already saw the ride
                # as CANCELLED (cancel_ride won before match_ride's first
                # read); InvalidRideStateError: cancel_ride won in the gap
                # between match_ride's initial check and its own conditional
                # UPDATE. Both are the same race, just caught at different
                # points -- expected on the losing ordering, either way.
                pass
        finally:
            session.close()

    def do_cancel():
        session = committing_session_factory()
        try:
            barrier.wait()
            try:
                cancel_ride(session, ride_id)
            except InvalidRideStateError:
                pass  # not expected to happen here, but not this test's concern
        finally:
            session.close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        f_match = executor.submit(do_match)
        f_cancel = executor.submit(do_cancel)
        f_match.result()
        f_cancel.result()

    check = committing_session_factory()
    try:
        final_ride = RideRepository(check).get_by_id(ride_id)
        final_driver = DriverRepository(check).get_by_id(driver_id)

        assert final_ride.status == RideStatus.CANCELLED
        assert final_driver.status == DriverStatus.AVAILABLE
    finally:
        check.close()
