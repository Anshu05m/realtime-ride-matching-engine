"""Proves the Postgres partial unique index (ux_rides_one_active_ride_per_driver)
holds on its own, bypassing app/matching/matcher.py and the Redis lock
entirely -- this is the actual durable correctness boundary Slice 3 relies
on; the lock is only there to reduce how often anyone has to hit it.

Requires `docker compose up -d postgres redis` running first.
"""

from sqlalchemy.exc import IntegrityError

from app.models.ride import RideStatus
from app.storage.repositories.driver_repository import DriverRepository
from app.storage.repositories.ride_repository import RideRepository
from app.storage.repositories.rider_repository import RiderRepository


def test_two_active_rides_for_the_same_driver_violates_the_constraint(db_session):
    driver = DriverRepository(db_session).create(current_lat=1.0, current_lng=1.0, h3_index="a")
    rider_repo = RiderRepository(db_session)
    ride_repo = RideRepository(db_session)

    rider_a = rider_repo.create(pickup_lat=1.0, pickup_lng=1.0)
    ride_a = ride_repo.create(rider_id=rider_a.id, pickup_lat=1.0, pickup_lng=1.0)
    ride_a.driver_id = driver.id
    ride_a.status = RideStatus.MATCHED
    db_session.flush()

    rider_b = rider_repo.create(pickup_lat=1.0, pickup_lng=1.0)
    ride_b = ride_repo.create(rider_id=rider_b.id, pickup_lat=1.0, pickup_lng=1.0)
    ride_b.driver_id = driver.id
    ride_b.status = RideStatus.MATCHED

    try:
        db_session.flush()
        raised = False
    except IntegrityError:
        raised = True
        db_session.rollback()

    assert raised, "expected the partial unique index to reject a second active ride"


def test_a_second_active_ride_is_allowed_once_the_first_completes(db_session):
    driver = DriverRepository(db_session).create(current_lat=1.0, current_lng=1.0, h3_index="a")
    rider_repo = RiderRepository(db_session)
    ride_repo = RideRepository(db_session)

    rider_a = rider_repo.create(pickup_lat=1.0, pickup_lng=1.0)
    ride_a = ride_repo.create(rider_id=rider_a.id, pickup_lat=1.0, pickup_lng=1.0)
    ride_a.driver_id = driver.id
    ride_a.status = RideStatus.MATCHED
    db_session.flush()

    ride_repo.update_status(ride_a.id, status=RideStatus.COMPLETED)

    rider_b = rider_repo.create(pickup_lat=1.0, pickup_lng=1.0)
    ride_b = ride_repo.create(rider_id=rider_b.id, pickup_lat=1.0, pickup_lng=1.0)
    ride_b.driver_id = driver.id
    ride_b.status = RideStatus.MATCHED
    db_session.flush()  # should NOT raise -- ride_a is no longer active

    assert ride_b.driver_id == driver.id


def test_two_different_drivers_can_each_have_an_active_ride(db_session):
    driver_repo = DriverRepository(db_session)
    driver_a = driver_repo.create(current_lat=1.0, current_lng=1.0, h3_index="a")
    driver_b = driver_repo.create(current_lat=2.0, current_lng=2.0, h3_index="b")
    rider_repo = RiderRepository(db_session)
    ride_repo = RideRepository(db_session)

    rider_a = rider_repo.create(pickup_lat=1.0, pickup_lng=1.0)
    ride_a = ride_repo.create(rider_id=rider_a.id, pickup_lat=1.0, pickup_lng=1.0)
    ride_a.driver_id = driver_a.id
    ride_a.status = RideStatus.MATCHED

    rider_b = rider_repo.create(pickup_lat=2.0, pickup_lng=2.0)
    ride_b = ride_repo.create(rider_id=rider_b.id, pickup_lat=2.0, pickup_lng=2.0)
    ride_b.driver_id = driver_b.id
    ride_b.status = RideStatus.MATCHED

    db_session.flush()  # should NOT raise -- different drivers

    assert ride_a.driver_id == driver_a.id
    assert ride_b.driver_id == driver_b.id
