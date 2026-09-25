"""Real-DB tests for idempotent ride creation.

Requires `docker compose up -d postgres redis` running first.
"""

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.config import settings
from app.models.driver import DriverStatus
from app.models.ride import Ride
from app.pricing.zones import zone_id_for_point
from app.services.ride_service import IdempotencyKeyConflictError, create_ride
from app.storage.repositories.driver_repository import DriverRepository
from app.storage.repositories.ride_repository import RideRepository
from app.storage.repositories.rider_repository import RiderRepository

LAT, LNG = 37.7749, -122.4194


def _make_rider(db_session):
    return RiderRepository(db_session).create(pickup_lat=LAT, pickup_lng=LNG)


def test_sequential_retries_with_same_key_return_the_same_ride(db_session):
    rider = _make_rider(db_session)

    first = create_ride(
        db_session, rider_id=rider.id, pickup_lat=LAT, pickup_lng=LNG, idempotency_key="key-1"
    )
    second = create_ride(
        db_session, rider_id=rider.id, pickup_lat=LAT, pickup_lng=LNG, idempotency_key="key-1"
    )
    third = create_ride(
        db_session, rider_id=rider.id, pickup_lat=LAT, pickup_lng=LNG, idempotency_key="key-1"
    )

    assert first.id == second.id == third.id

    matching_rows = list(
        db_session.scalars(select(Ride).where(Ride.idempotency_key == "key-1"))
    )
    assert len(matching_rows) == 1


def test_retry_after_the_original_write_already_committed(db_session):
    """"Partial failure" here means specifically: the original insert
    committed, but the caller never found out (e.g. the response was lost).
    A retry with the same key + same params must return that already-
    committed ride, not error or duplicate it. (A true mid-transaction crash
    needs no special handling -- nothing durable exists yet, so a retry
    after that legitimately creates a fresh ride; that's covered by the
    plain "unseen key" path, not this test.)"""
    rider = _make_rider(db_session)
    original = RideRepository(db_session).create(
        rider_id=rider.id, pickup_lat=LAT, pickup_lng=LNG, idempotency_key="key-2"
    )
    db_session.commit()

    retried = create_ride(
        db_session, rider_id=rider.id, pickup_lat=LAT, pickup_lng=LNG, idempotency_key="key-2"
    )

    assert retried.id == original.id


def test_calls_without_a_key_each_create_a_new_ride(db_session):
    rider = _make_rider(db_session)

    first = create_ride(db_session, rider_id=rider.id, pickup_lat=LAT, pickup_lng=LNG)
    second = create_ride(db_session, rider_id=rider.id, pickup_lat=LAT, pickup_lng=LNG)

    assert first.id != second.id


def test_reusing_a_key_with_different_params_raises_conflict(db_session):
    rider = _make_rider(db_session)
    create_ride(
        db_session, rider_id=rider.id, pickup_lat=LAT, pickup_lng=LNG, idempotency_key="key-3"
    )

    try:
        create_ride(
            db_session,
            rider_id=rider.id,
            pickup_lat=LAT + 1.0,
            pickup_lng=LNG,
            idempotency_key="key-3",
        )
        raised = False
    except IdempotencyKeyConflictError:
        raised = True

    assert raised


def test_reusing_a_key_with_a_different_rider_raises_conflict(db_session):
    rider_a = _make_rider(db_session)
    rider_b = _make_rider(db_session)
    create_ride(
        db_session, rider_id=rider_a.id, pickup_lat=LAT, pickup_lng=LNG, idempotency_key="key-4"
    )

    try:
        create_ride(
            db_session,
            rider_id=rider_b.id,
            pickup_lat=LAT,
            pickup_lng=LNG,
            idempotency_key="key-4",
        )
        raised = False
    except IdempotencyKeyConflictError:
        raised = True

    assert raised


def test_two_direct_inserts_with_the_same_key_violate_the_constraint(db_session):
    """Proves the DB constraint alone rejects duplicates, independent of
    create_ride's own check -- the actual correctness backstop, same pattern
    as Slice 3's test_ride_constraints.py."""
    rider = _make_rider(db_session)
    ride_repo = RideRepository(db_session)

    ride_repo.create(rider_id=rider.id, pickup_lat=LAT, pickup_lng=LNG, idempotency_key="key-5")

    try:
        ride_repo.create(
            rider_id=rider.id, pickup_lat=LAT, pickup_lng=LNG, idempotency_key="key-5"
        )
        raised = False
    except IntegrityError:
        raised = True
        db_session.rollback()

    assert raised


def test_multiple_rides_with_no_key_never_collide(db_session):
    rider = _make_rider(db_session)
    ride_repo = RideRepository(db_session)

    a = ride_repo.create(rider_id=rider.id, pickup_lat=LAT, pickup_lng=LNG)
    b = ride_repo.create(rider_id=rider.id, pickup_lat=LAT, pickup_lng=LNG)

    assert a.idempotency_key is None
    assert b.idempotency_key is None
    assert a.id != b.id


# --- Slice 5: zone + surge quoting ------------------------------------------


def test_create_ride_stores_zone_and_surge_multiplier(db_session):
    rider = _make_rider(db_session)

    ride = create_ride(db_session, rider_id=rider.id, pickup_lat=LAT, pickup_lng=LNG)

    expected_zone = zone_id_for_point(LAT, LNG, settings.surge_zone_resolution)
    assert ride.zone_id == expected_zone
    assert ride.surge_multiplier is not None
    assert float(ride.surge_multiplier) >= 1.0


def test_a_created_ride_does_not_inflate_its_own_quoted_multiplier(db_session):
    """Regression test for the ordering requirement in create_ride: zone/
    surge must be computed BEFORE the new Ride row is written, or a ride
    would count itself in its own demand snapshot."""
    rider = _make_rider(db_session)
    DriverRepository(db_session).create(
        current_lat=LAT,
        current_lng=LNG,
        h3_index="h3-fixed",
        zone_id=zone_id_for_point(LAT, LNG, settings.surge_zone_resolution),
        status=DriverStatus.AVAILABLE,
    )

    ride = create_ride(db_session, rider_id=rider.id, pickup_lat=LAT, pickup_lng=LNG)

    # One driver, zero pre-existing demand: ratio should be 0/1, i.e. the
    # floor multiplier -- if this ride had counted itself, demand would be
    # 1 instead of 0 and the multiplier would be inflated above the floor.
    assert float(ride.surge_multiplier) == 1.0
