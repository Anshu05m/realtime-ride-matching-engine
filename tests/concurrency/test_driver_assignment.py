"""Mandatory concurrency tests (CLAUDE.md section 14): fire many simultaneous
ride requests at the same small candidate pool and prove the invariant --
at most one active ride per driver, ever -- holds regardless of how many
requests raced for it.

Requires `docker compose up -d postgres redis` running first.
"""

import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

import pytest

from app.config import settings
from app.matching.geo import latlng_to_h3
from app.matching.matcher import NoAvailableDriverError, match_ride
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


def _run_concurrent_matches(committing_session_factory, ride_ids, on_result):
    """Fires match_ride for each ride_id from its own thread/session,
    synchronized with a barrier so requests actually race instead of
    serializing via Python's own scheduling. on_result(index, ride_or_None,
    error_or_None) records the outcome."""
    concurrency = len(ride_ids)
    barrier = threading.Barrier(concurrency)

    def worker(index, ride_id):
        session = committing_session_factory()
        try:
            barrier.wait()
            ride = RideRepository(session).get_by_id(ride_id)
            try:
                matched = match_ride(session, ride)
                on_result(index, matched, None)
            except NoAvailableDriverError as exc:
                on_result(index, None, exc)
        finally:
            session.close()

    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        futures = [executor.submit(worker, i, rid) for i, rid in enumerate(ride_ids)]
        for future in as_completed(futures):
            future.result()  # re-raises any unexpected worker exception


@pytest.mark.parametrize("concurrency", [5, 20, 50])
def test_exactly_one_request_wins_a_single_contested_driver(
    committing_session_factory, concurrency
):
    setup = committing_session_factory()
    driver = _make_driver(setup)
    ride_ids = [_make_requested_ride(setup).id for _ in range(concurrency)]
    setup.commit()
    setup.close()

    results: list[str] = [None] * concurrency

    def record(index, matched, error):
        results[index] = "matched" if matched is not None else "no_driver"

    _run_concurrent_matches(committing_session_factory, ride_ids, record)

    assert results.count("matched") == 1
    assert results.count("no_driver") == concurrency - 1

    check = committing_session_factory()
    try:
        active = RideRepository(check).list_active_for_driver(driver.id)
        assert len(active) == 1
    finally:
        check.close()


@pytest.mark.parametrize("concurrency", [10, 25])
def test_losers_fall_through_to_other_available_drivers(committing_session_factory, concurrency):
    setup = committing_session_factory()
    num_drivers = 3
    drivers = [_make_driver(setup) for _ in range(num_drivers)]
    ride_ids = [_make_requested_ride(setup).id for _ in range(concurrency)]
    setup.commit()
    setup.close()

    results: list = [None] * concurrency

    def record(index, matched, error):
        results[index] = matched.driver_id if matched is not None else None

    _run_concurrent_matches(committing_session_factory, ride_ids, record)

    successful = [driver_id for driver_id in results if driver_id is not None]
    assert len(successful) == min(concurrency, num_drivers)
    assert len(set(successful)) == len(successful), "no driver should be assigned twice"

    check = committing_session_factory()
    try:
        ride_repo = RideRepository(check)
        for driver in drivers:
            assert len(ride_repo.list_active_for_driver(driver.id)) <= 1
    finally:
        check.close()
