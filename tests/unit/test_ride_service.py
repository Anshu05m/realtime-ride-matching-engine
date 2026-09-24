"""Pure branching-logic tests for create_ride, against a fake repository --
no database needed. The race-handling itself (what happens when two real,
concurrently-committing sessions collide) can't be meaningfully tested this
way; see tests/integration/test_ride_service.py and
tests/concurrency/test_idempotent_ride_creation.py for that.
"""

import uuid
from unittest.mock import MagicMock, patch

import pytest

from app.models.ride import Ride, RideStatus
from app.services.ride_service import IdempotencyKeyConflictError, create_ride

RIDER_ID = uuid.uuid4()
LAT, LNG = 37.7749, -122.4194


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


@patch("app.services.ride_service.RideRepository")
def test_no_key_creates_without_checking_or_committing(mock_repo_cls):
    mock_repo = mock_repo_cls.return_value
    mock_repo.create.return_value = _fake_ride()
    db = MagicMock()

    create_ride(db, rider_id=RIDER_ID, pickup_lat=LAT, pickup_lng=LNG)

    mock_repo.get_by_idempotency_key.assert_not_called()
    mock_repo.create.assert_called_once()
    db.commit.assert_not_called()


@patch("app.services.ride_service.RideRepository")
def test_unseen_key_creates_and_commits(mock_repo_cls):
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


@patch("app.services.ride_service.RideRepository")
def test_integrity_error_on_commit_rolls_back_and_returns_the_winner(mock_repo_cls):
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
