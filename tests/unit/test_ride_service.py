"""Pure branching-logic tests for create_ride, against a fake repository --
no database needed. The race-handling itself (what happens when two real,
concurrently-committing sessions collide) can't be meaningfully tested this
way; see tests/integration/test_ride_service.py and
tests/concurrency/test_idempotent_ride_creation.py for that.
"""

import uuid
from unittest.mock import MagicMock, patch

import pytest

from app.models.driver import Driver, DriverStatus
from app.models.ride import InvalidRideStateError, Ride, RideNotFoundError, RideStatus
from app.pricing.surge import SurgeResult
from app.services.ride_service import (
    IdempotencyKeyConflictError,
    cancel_ride,
    complete_ride,
    create_ride,
)
from app.storage.repositories.ride_repository import ACTIVE_RIDE_STATUSES

RIDER_ID = uuid.uuid4()
LAT, LNG = 37.7749, -122.4194

# create_ride calls the real (DB-free) zone_id_for_point but the real
# zone_surge would hit the database through its own repository instances --
# not the RideRepository patched below, since pricing/surge.py imports its
# own. Patch it directly wherever create_ride actually reaches it (i.e. on
# every path that isn't short-circuited by an idempotency-key hit/conflict).
_FAKE_SURGE = SurgeResult(zone_id="fake-zone", demand=0, supply=1, multiplier=1.0)


def _fake_driver(**overrides) -> Driver:
    defaults = dict(
        id=uuid.uuid4(),
        current_lat=LAT,
        current_lng=LNG,
        h3_index="h3-fixed",
        zone_id="zone-fixed",
        status=DriverStatus.BUSY,
    )
    defaults.update(overrides)
    return Driver(**defaults)


def _fake_ride(**overrides) -> Ride:
    defaults = dict(
        id=uuid.uuid4(),
        rider_id=RIDER_ID,
        pickup_lat=LAT,
        pickup_lng=LNG,
        status=RideStatus.REQUESTED,
    )
    defaults.update(overrides)
    return Ride(**defaults)


@patch("app.services.ride_service.zone_surge", return_value=_FAKE_SURGE)
@patch("app.services.ride_service.RideRepository")
def test_no_key_creates_and_commits_unconditionally(mock_repo_cls, mock_zone_surge):
    # Slice 6: create_ride commits even without an idempotency key, since the
    # FastAPI get_db() dependency never auto-commits -- see the module
    # docstring's transaction-boundary note for why this changed from Slice 4.
    mock_repo = mock_repo_cls.return_value
    mock_repo.create.return_value = _fake_ride()
    db = MagicMock()

    create_ride(db, rider_id=RIDER_ID, pickup_lat=LAT, pickup_lng=LNG)

    mock_repo.get_by_idempotency_key.assert_not_called()
    mock_repo.create.assert_called_once()
    db.commit.assert_called_once()


@patch("app.services.ride_service.zone_surge", return_value=_FAKE_SURGE)
@patch("app.services.ride_service.RideRepository")
def test_unseen_key_creates_and_commits(mock_repo_cls, mock_zone_surge):
    mock_repo = mock_repo_cls.return_value
    mock_repo.get_by_idempotency_key.return_value = None
    mock_repo.create.return_value = _fake_ride()
    db = MagicMock()

    create_ride(db, rider_id=RIDER_ID, pickup_lat=LAT, pickup_lng=LNG, idempotency_key="abc123")

    mock_repo.create.assert_called_once()
    db.commit.assert_called_once()


@patch("app.services.ride_service.RideRepository")
def test_known_key_with_matching_params_returns_existing_without_creating(mock_repo_cls):
    mock_repo = mock_repo_cls.return_value
    existing = _fake_ride()
    mock_repo.get_by_idempotency_key.return_value = existing
    db = MagicMock()

    result = create_ride(
        db, rider_id=RIDER_ID, pickup_lat=LAT, pickup_lng=LNG, idempotency_key="abc123"
    )

    assert result is existing
    mock_repo.create.assert_not_called()
    db.commit.assert_not_called()


@patch("app.services.ride_service.RideRepository")
def test_known_key_with_mismatched_params_raises_without_creating(mock_repo_cls):
    mock_repo = mock_repo_cls.return_value
    existing = _fake_ride(pickup_lat=1.0, pickup_lng=1.0)
    mock_repo.get_by_idempotency_key.return_value = existing
    db = MagicMock()

    with pytest.raises(IdempotencyKeyConflictError):
        create_ride(
            db, rider_id=RIDER_ID, pickup_lat=LAT, pickup_lng=LNG, idempotency_key="abc123"
        )

    mock_repo.create.assert_not_called()
    db.commit.assert_not_called()


@patch("app.services.ride_service.RideRepository")
def test_mismatched_rider_id_also_raises(mock_repo_cls):
    mock_repo = mock_repo_cls.return_value
    existing = _fake_ride(rider_id=uuid.uuid4())
    mock_repo.get_by_idempotency_key.return_value = existing
    db = MagicMock()

    with pytest.raises(IdempotencyKeyConflictError):
        create_ride(
            db, rider_id=RIDER_ID, pickup_lat=LAT, pickup_lng=LNG, idempotency_key="abc123"
        )


@patch("app.services.ride_service.zone_surge", return_value=_FAKE_SURGE)
@patch("app.services.ride_service.RideRepository")
def test_integrity_error_on_commit_rolls_back_and_returns_the_winner(mock_repo_cls, mock_zone_surge):
    from sqlalchemy.exc import IntegrityError

    mock_repo = mock_repo_cls.return_value
    mock_repo.get_by_idempotency_key.side_effect = [None, _fake_ride()]
    mock_repo.create.return_value = _fake_ride()
    db = MagicMock()
    db.commit.side_effect = IntegrityError("stmt", {}, Exception("unique violation"))

    result = create_ride(
        db, rider_id=RIDER_ID, pickup_lat=LAT, pickup_lng=LNG, idempotency_key="abc123"
    )

    db.rollback.assert_called_once()
    assert result is not None
    assert mock_repo.get_by_idempotency_key.call_count == 2


@patch("app.services.ride_service.zone_surge", return_value=_FAKE_SURGE)
@patch("app.services.ride_service.RideRepository")
def test_integrity_error_with_no_key_is_re_raised_not_swallowed(mock_repo_cls, mock_zone_surge):
    # A keyless ride can't legitimately hit the idempotency-key uniqueness
    # race, so an IntegrityError here is something else -- it must propagate,
    # not be treated as "someone else won" (which would also risk calling
    # get_by_idempotency_key(None), silently matching an unrelated null-key
    # row via "IS NULL").
    from sqlalchemy.exc import IntegrityError

    mock_repo = mock_repo_cls.return_value
    mock_repo.create.return_value = _fake_ride()
    db = MagicMock()
    db.commit.side_effect = IntegrityError("stmt", {}, Exception("some other violation"))

    with pytest.raises(IntegrityError):
        create_ride(db, rider_id=RIDER_ID, pickup_lat=LAT, pickup_lng=LNG)

    db.rollback.assert_called_once()
    mock_repo.get_by_idempotency_key.assert_not_called()


# --- cancel_ride / complete_ride ---------------------------------------
#
# These test the branching logic and repo wiring against a mocked
# RideRepository/DriverRepository -- not the atomic conditional UPDATE's own
# correctness (that's tests/integration/test_ride_repository.py, which needs
# a real database to prove a real rowcount) or genuine cross-session races
# (tests/concurrency/test_ride_lifecycle.py).


@patch("app.services.ride_service.DriverRepository")
@patch("app.services.ride_service.RideRepository")
def test_cancel_ride_raises_not_found_when_missing(mock_ride_repo_cls, mock_driver_repo_cls):
    mock_ride_repo = mock_ride_repo_cls.return_value
    mock_ride_repo.get_by_id.return_value = None
    db = MagicMock()

    with pytest.raises(RideNotFoundError):
        cancel_ride(db, uuid.uuid4())


@patch("app.services.ride_service.DriverRepository")
@patch("app.services.ride_service.RideRepository")
def test_cancel_ride_requests_the_correct_valid_source_statuses(
    mock_ride_repo_cls, mock_driver_repo_cls
):
    mock_ride_repo = mock_ride_repo_cls.return_value
    ride = _fake_ride(status=RideStatus.MATCHED)
    mock_ride_repo.get_by_id.return_value = ride
    mock_ride_repo.transition_status.return_value = True
    db = MagicMock()

    cancel_ride(db, ride.id)

    _, kwargs = mock_ride_repo.transition_status.call_args
    assert kwargs["from_statuses"] == ACTIVE_RIDE_STATUSES
    assert kwargs["to_status"] == RideStatus.CANCELLED


@patch("app.services.ride_service.DriverRepository")
@patch("app.services.ride_service.RideRepository")
def test_cancel_ride_raises_invalid_state_reporting_the_real_current_status(
    mock_ride_repo_cls, mock_driver_repo_cls
):
    # Simulates losing the atomic-UPDATE race: transition_status returns
    # False, and the re-fetch that follows sees whatever another writer
    # already committed.
    mock_ride_repo = mock_ride_repo_cls.return_value
    ride_id = uuid.uuid4()
    mock_ride_repo.get_by_id.side_effect = [
        _fake_ride(id=ride_id, status=RideStatus.MATCHED),
        _fake_ride(id=ride_id, status=RideStatus.COMPLETED),
    ]
    mock_ride_repo.transition_status.return_value = False
    db = MagicMock()

    with pytest.raises(InvalidRideStateError, match="completed"):
        cancel_ride(db, ride_id)

    db.rollback.assert_called_once()


@patch("app.services.ride_service.DriverRepository")
@patch("app.services.ride_service.RideRepository")
def test_cancel_ride_frees_the_assigned_driver(mock_ride_repo_cls, mock_driver_repo_cls):
    driver_id = uuid.uuid4()
    mock_ride_repo = mock_ride_repo_cls.return_value
    ride = _fake_ride(status=RideStatus.MATCHED, driver_id=driver_id)
    mock_ride_repo.get_by_id.return_value = ride
    mock_ride_repo.transition_status.return_value = True

    mock_driver_repo = mock_driver_repo_cls.return_value
    driver = _fake_driver(id=driver_id, status=DriverStatus.BUSY)
    mock_driver_repo.get_by_id.return_value = driver

    db = MagicMock()
    cancel_ride(db, ride.id)

    assert driver.status == DriverStatus.AVAILABLE
    mock_driver_repo.get_by_id.assert_called_once_with(driver_id, fresh=True)
    db.commit.assert_called_once()


@patch("app.services.ride_service.DriverRepository")
@patch("app.services.ride_service.RideRepository")
def test_cancel_ride_with_no_driver_never_touches_driver_repo(
    mock_ride_repo_cls, mock_driver_repo_cls
):
    mock_ride_repo = mock_ride_repo_cls.return_value
    ride = _fake_ride(status=RideStatus.REQUESTED, driver_id=None)
    mock_ride_repo.get_by_id.return_value = ride
    mock_ride_repo.transition_status.return_value = True
    mock_driver_repo = mock_driver_repo_cls.return_value

    db = MagicMock()
    cancel_ride(db, ride.id)

    mock_driver_repo.get_by_id.assert_not_called()


@patch("app.services.ride_service.DriverRepository")
@patch("app.services.ride_service.RideRepository")
def test_complete_ride_raises_not_found_when_missing(mock_ride_repo_cls, mock_driver_repo_cls):
    mock_ride_repo = mock_ride_repo_cls.return_value
    mock_ride_repo.get_by_id.return_value = None
    db = MagicMock()

    with pytest.raises(RideNotFoundError):
        complete_ride(db, uuid.uuid4())


@patch("app.services.ride_service.DriverRepository")
@patch("app.services.ride_service.RideRepository")
def test_complete_ride_requests_the_correct_valid_source_statuses(
    mock_ride_repo_cls, mock_driver_repo_cls
):
    mock_ride_repo = mock_ride_repo_cls.return_value
    ride = _fake_ride(status=RideStatus.MATCHED, driver_id=uuid.uuid4())
    mock_ride_repo.get_by_id.return_value = ride
    mock_ride_repo.transition_status.return_value = True
    mock_driver_repo_cls.return_value.get_by_id.return_value = None
    db = MagicMock()

    complete_ride(db, ride.id)

    _, kwargs = mock_ride_repo.transition_status.call_args
    assert kwargs["from_statuses"] == (RideStatus.MATCHED, RideStatus.IN_PROGRESS)
    assert kwargs["to_status"] == RideStatus.COMPLETED


@patch("app.services.ride_service.DriverRepository")
@patch("app.services.ride_service.RideRepository")
def test_complete_ride_rejects_a_driverless_requested_ride(
    mock_ride_repo_cls, mock_driver_repo_cls
):
    mock_ride_repo = mock_ride_repo_cls.return_value
    ride_id = uuid.uuid4()
    mock_ride_repo.get_by_id.side_effect = [
        _fake_ride(id=ride_id, status=RideStatus.REQUESTED),
        _fake_ride(id=ride_id, status=RideStatus.REQUESTED),
    ]
    mock_ride_repo.transition_status.return_value = False
    db = MagicMock()

    with pytest.raises(InvalidRideStateError, match="requested"):
        complete_ride(db, ride_id)


@patch("app.services.ride_service.DriverRepository")
@patch("app.services.ride_service.RideRepository")
def test_complete_ride_frees_the_assigned_driver(mock_ride_repo_cls, mock_driver_repo_cls):
    driver_id = uuid.uuid4()
    mock_ride_repo = mock_ride_repo_cls.return_value
    ride = _fake_ride(status=RideStatus.MATCHED, driver_id=driver_id)
    mock_ride_repo.get_by_id.return_value = ride
    mock_ride_repo.transition_status.return_value = True

    mock_driver_repo = mock_driver_repo_cls.return_value
    driver = _fake_driver(id=driver_id, status=DriverStatus.BUSY)
    mock_driver_repo.get_by_id.return_value = driver

    db = MagicMock()
    complete_ride(db, ride.id)

    assert driver.status == DriverStatus.AVAILABLE
    db.commit.assert_called_once()
