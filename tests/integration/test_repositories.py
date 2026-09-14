"""Repository tests against a real Postgres database.

Requires `docker compose up -d postgres redis` running first.
"""

from app.models.driver import DriverStatus
from app.models.ride import RideStatus
from app.storage.repositories.driver_repository import DriverRepository
from app.storage.repositories.ride_repository import RideRepository
from app.storage.repositories.rider_repository import RiderRepository


def test_create_and_get_driver(db_session):
    repo = DriverRepository(db_session)

    created = repo.create(current_lat=37.7749, current_lng=-122.4194, h3_index="8928308280fffff")

    fetched = repo.get_by_id(created.id)
    assert fetched is not None
    assert fetched.id == created.id
    assert fetched.status == DriverStatus.AVAILABLE
    assert fetched.h3_index == "8928308280fffff"


def test_update_driver_location(db_session):
    repo = DriverRepository(db_session)
    driver = repo.create(current_lat=0.0, current_lng=0.0, h3_index="8928308280fffff")

    updated = repo.update_location(driver.id, lat=1.0, lng=2.0, h3_index="8928308281fffff")

    assert updated is not None
    assert updated.current_lat == 1.0
    assert updated.current_lng == 2.0
    assert updated.h3_index == "8928308281fffff"


def test_update_driver_status(db_session):
    repo = DriverRepository(db_session)
    driver = repo.create(current_lat=0.0, current_lng=0.0, h3_index="8928308280fffff")

    updated = repo.update_status(driver.id, status=DriverStatus.BUSY)

    assert updated is not None
    assert updated.status == DriverStatus.BUSY


def test_list_drivers_by_status(db_session):
    repo = DriverRepository(db_session)
    available = repo.create(current_lat=0.0, current_lng=0.0, h3_index="a")
    busy = repo.create(current_lat=0.0, current_lng=0.0, h3_index="b", status=DriverStatus.BUSY)

    available_drivers = repo.list_by_status(DriverStatus.AVAILABLE)
    busy_drivers = repo.list_by_status(DriverStatus.BUSY)

    assert available.id in {d.id for d in available_drivers}
    assert busy.id not in {d.id for d in available_drivers}
    assert busy.id in {d.id for d in busy_drivers}


def test_get_unknown_driver_returns_none(db_session):
    import uuid

    repo = DriverRepository(db_session)
    assert repo.get_by_id(uuid.uuid4()) is None


def test_create_and_get_rider(db_session):
    repo = RiderRepository(db_session)

    created = repo.create(pickup_lat=37.7749, pickup_lng=-122.4194)

    fetched = repo.get_by_id(created.id)
    assert fetched is not None
    assert fetched.pickup_lat == 37.7749


def test_create_ride_defaults_to_requested_and_no_driver(db_session):
    rider = RiderRepository(db_session).create(pickup_lat=1.0, pickup_lng=1.0)
    repo = RideRepository(db_session)

    ride = repo.create(rider_id=rider.id, pickup_lat=1.0, pickup_lng=1.0)

    assert ride.status == RideStatus.REQUESTED
    assert ride.driver_id is None
    assert ride.fare is None


def test_list_active_rides_for_driver(db_session):
    rider = RiderRepository(db_session).create(pickup_lat=1.0, pickup_lng=1.0)
    driver = DriverRepository(db_session).create(current_lat=1.0, current_lng=1.0, h3_index="a")
    ride_repo = RideRepository(db_session)

    ride = ride_repo.create(rider_id=rider.id, pickup_lat=1.0, pickup_lng=1.0)
    ride.driver_id = driver.id
    ride.status = RideStatus.MATCHED
    db_session.flush()

    active = ride_repo.list_active_for_driver(driver.id)
    assert [r.id for r in active] == [ride.id]

    ride_repo.update_status(ride.id, status=RideStatus.COMPLETED)
    assert ride_repo.list_active_for_driver(driver.id) == []


def test_ride_requires_valid_rider_id(db_session):
    import uuid

    from sqlalchemy.exc import IntegrityError

    repo = RideRepository(db_session)

    try:
        repo.create(rider_id=uuid.uuid4(), pickup_lat=1.0, pickup_lng=1.0)
        raised = False
    except IntegrityError:
        raised = True
        db_session.rollback()

    assert raised, "expected a foreign key violation for a nonexistent rider_id"
