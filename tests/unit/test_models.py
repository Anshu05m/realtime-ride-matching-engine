"""Pure Python checks on the model definitions — no database required."""

from sqlalchemy import inspect

from app.models.driver import Driver, DriverStatus
from app.models.ride import Ride, RideStatus
from app.models.rider import Rider


def test_driver_status_values():
    assert {s.value for s in DriverStatus} == {"available", "busy", "offline"}


def test_ride_status_values():
    assert {s.value for s in RideStatus} == {
        "requested",
        "matched",
        "in_progress",
        "completed",
        "cancelled",
    }


def test_driver_columns_match_spec():
    columns = {c.name for c in inspect(Driver).columns}
    assert columns == {
        "id",
        "current_lat",
        "current_lng",
        "h3_index",
        "zone_id",
        "status",
        "last_updated_at",
    }


def test_rider_columns_match_spec():
    columns = {c.name for c in inspect(Rider).columns}
    assert columns == {"id", "pickup_lat", "pickup_lng", "requested_at"}


def test_ride_columns_match_spec():
    columns = {c.name for c in inspect(Ride).columns}
    assert columns == {
        "id",
        "rider_id",
        "driver_id",
        "status",
        "pickup_lat",
        "pickup_lng",
        "matched_at",
        "fare",
        "surge_multiplier",
        "zone_id",
        "created_at",
        "updated_at",
        "idempotency_key",
    }


def test_ride_driver_id_is_nullable():
    # A ride can exist in "requested" status before any driver is matched.
    driver_id_column = inspect(Ride).columns["driver_id"]
    assert driver_id_column.nullable is True


def test_driver_default_status_is_available():
    # SQLAlchemy column defaults apply at flush time, not on __init__, so this
    # checks the configured default rather than instantiating without a session.
    status_column = inspect(Driver).columns["status"]
    assert status_column.default.arg == DriverStatus.AVAILABLE
