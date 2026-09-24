"""Mandatory concurrency test for Slice 4: many simultaneous requests using
the same idempotency key must all resolve to the same logical ride, with
exactly one row ever created for that key.

Requires `docker compose up -d postgres redis` running first.
"""

import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

import pytest
from sqlalchemy import select

from app.models.ride import Ride
from app.services.ride_service import create_ride
from app.storage.repositories.rider_repository import RiderRepository

LAT, LNG = 37.7749, -122.4194


@pytest.mark.parametrize("concurrency", [5, 20, 50])
def test_concurrent_retries_with_the_same_key_all_resolve_to_one_ride(
    committing_session_factory, concurrency
):
    setup = committing_session_factory()
    rider = RiderRepository(setup).create(pickup_lat=LAT, pickup_lng=LNG)
    setup.commit()
    rider_id = rider.id
    setup.close()

    key = "concurrent-key"
    barrier = threading.Barrier(concurrency)
    results: list = [None] * concurrency

    def worker(index):
        session = committing_session_factory()
        try:
            barrier.wait()
            ride = create_ride(
                session,
                rider_id=rider_id,
                pickup_lat=LAT,
                pickup_lng=LNG,
                idempotency_key=key,
            )
            results[index] = ride.id
        finally:
            session.close()

    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        futures = [executor.submit(worker, i) for i in range(concurrency)]
        for future in as_completed(futures):
            future.result()

    assert len(set(results)) == 1, "every concurrent caller must get back the same ride id"

    check = committing_session_factory()
    try:
        rows = list(check.scalars(select(Ride).where(Ride.idempotency_key == key)))
        assert len(rows) == 1
    finally:
        check.close()
