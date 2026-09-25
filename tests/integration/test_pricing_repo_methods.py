"""Real-DB tests for the two repository count methods Slice 5's surge
formula is built on.

Requires `docker compose up -d postgres redis` running first.
"""

from datetime import UTC, datetime, timedelta

from app.models.driver import DriverStatus
from app.models.ride import RideStatus
from app.storage.repositories.driver_repository import DriverRepository
from app.storage.repositories.ride_repository import RideRepository
from app.storage.repositories.rider_repository import RiderRepository

ZONE_A = "zone-a"
ZONE_B = "zone-b"
LAT, LNG = 37.7749, -122.4194


def _make_driver(db_session, *, zone_id=ZONE_A, status=DriverStatus.AVAILABLE):
    return DriverRepository(db_session).create(
        current_lat=LAT, current_lng=LNG, h3_index="h3-fixed", zone_id=zone_id, status=status
    )


def _make_ride(db_session, *, zone_id=ZONE_A, status=RideStatus.REQUESTED, created_at=None):
    rider = RiderRepository(db_session).create(pickup_lat=LAT, pickup_lng=LNG)
    ride = RideRepository(db_session).create(
        rider_id=rider.id, pickup_lat=LAT, pickup_lng=LNG, zone_id=zone_id
    )
    ride.status = status
    if created_at is not None:
        ride.created_at = created_at
    db_session.flush()
    return ride


# --- supply (DriverRepository.count_available_in_zone) ---------------------


def test_counts_only_available_drivers_in_the_zone(db_session):
    _make_driver(db_session, zone_id=ZONE_A, status=DriverStatus.AVAILABLE)
    _make_driver(db_session, zone_id=ZONE_A, status=DriverStatus.BUSY)
    _make_driver(db_session, zone_id=ZONE_B, status=DriverStatus.AVAILABLE)

    count = DriverRepository(db_session).count_available_in_zone(ZONE_A)

    assert count == 1


def test_zone_with_no_drivers_counts_zero(db_session):
    assert DriverRepository(db_session).count_available_in_zone(ZONE_A) == 0


# --- demand (RideRepository.count_recent_active_in_zone) -------------------


def test_counts_only_active_rides_in_the_zone_within_the_window(db_session):
    now = datetime.now(UTC)
    since = now - timedelta(seconds=300)

    _make_ride(db_session, zone_id=ZONE_A, status=RideStatus.REQUESTED)
    _make_ride(db_session, zone_id=ZONE_A, status=RideStatus.MATCHED)
    _make_ride(db_session, zone_id=ZONE_A, status=RideStatus.COMPLETED)  # excluded: not active
    _make_ride(db_session, zone_id=ZONE_B, status=RideStatus.REQUESTED)  # excluded: other zone

    count = RideRepository(db_session).count_recent_active_in_zone(ZONE_A, since=since)

    assert count == 2


def test_excludes_rides_older_than_the_window(db_session):
    now = datetime.now(UTC)
    since = now - timedelta(seconds=300)

    _make_ride(
        db_session,
        zone_id=ZONE_A,
        status=RideStatus.REQUESTED,
        created_at=now - timedelta(seconds=301),
    )

    count = RideRepository(db_session).count_recent_active_in_zone(ZONE_A, since=since)

    assert count == 0


def test_includes_a_ride_right_at_the_window_boundary(db_session):
    now = datetime.now(UTC)
    since = now - timedelta(seconds=300)

    _make_ride(db_session, zone_id=ZONE_A, status=RideStatus.REQUESTED, created_at=since)

    count = RideRepository(db_session).count_recent_active_in_zone(ZONE_A, since=since)

    assert count == 1


def test_cancelled_rides_do_not_count_as_demand(db_session):
    now = datetime.now(UTC)
    since = now - timedelta(seconds=300)

    _make_ride(db_session, zone_id=ZONE_A, status=RideStatus.CANCELLED)

    count = RideRepository(db_session).count_recent_active_in_zone(ZONE_A, since=since)

    assert count == 0
