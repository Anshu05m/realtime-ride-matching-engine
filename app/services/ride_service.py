"""Idempotent ride creation (Slice 4).

Clients retry requests when they can't tell whether the original one
succeeded (dropped connection, timeout, crash before reading the response).
Without protection, a retried `POST /rides` creates a second ride for the
same intent. An idempotency key lets the caller mark "this is the same
creation attempt as before" -- retrying with the same key must return the
same logical ride, not create a duplicate.

The actual guarantee is a Postgres unique constraint on
`rides.idempotency_key` (uq_rides_idempotency_key, see
app/models/ride.py and the Slice 4 migration), the same pattern Slice 3
used: application logic reduces how often two callers collide, but the
database is what makes "no duplicate ride for one key" true even if the
application logic has a bug. No Redis lock is used here (contrast with
Slice 3's driver-assignment lock) -- there's no multi-step sequence to
serialize, just one insert whose outcome Postgres's constraint resolves
atomically in a single statement.

Transaction boundary note: create_ride commits its own successful insert,
a deliberate exception to this codebase's normal "caller owns commit"
default (see app/matching/matcher.py for the first such exception, and why
this is a second, separate instance of the same *shape* of constraint, not
the same reason). Here specifically: when two callers race on the same key,
Postgres makes the losing INSERT block at the row-lock level until the
winning transaction actually ends (commit or rollback) -- not merely
flushes. If this function only flushed and left committing to its caller,
the loser would stay blocked on whatever unrelated work that caller does
before it gets around to committing, and a caller that forgets to commit
would leave an "idempotent" ride silently non-durable. Owning the commit is
what deterministically unblocks a concurrent racer on the same key.
"""

import uuid

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.ride import Ride
from app.storage.repositories.ride_repository import RideRepository


class IdempotencyKeyConflictError(Exception):
    """Raised when a retried idempotency key is reused with different ride
    parameters than the original request. This compares only the fields
    already stored on the ride (rider_id, pickup_lat, pickup_lng) -- a
    deliberately bounded check, not a general hash of the full request
    payload. It won't catch a mismatch in some future field added later
    without updating this comparison."""


def _matches(ride: Ride, *, rider_id: uuid.UUID, pickup_lat: float, pickup_lng: float) -> bool:
    return (
        ride.rider_id == rider_id
        and ride.pickup_lat == pickup_lat
        and ride.pickup_lng == pickup_lng
    )


def create_ride(
    db: Session,
    *,
    rider_id: uuid.UUID,
    pickup_lat: float,
    pickup_lng: float,
    idempotency_key: str | None = None,
) -> Ride:
    ride_repo = RideRepository(db)

    if idempotency_key is not None:
        existing = ride_repo.get_by_idempotency_key(idempotency_key)
        if existing is not None:
            if not _matches(
                existing, rider_id=rider_id, pickup_lat=pickup_lat, pickup_lng=pickup_lng
            ):
                raise IdempotencyKeyConflictError(
                    f"idempotency key {idempotency_key!r} was already used for a "
                    f"different ride ({existing.id}) with different parameters"
                )
            # Return the ride's current state, not a snapshot of creation
            # time -- a retry of an already-matched/completed ride correctly
            # returns that ride as it is now.
            return existing

    if idempotency_key is None:
        # No dedup requested -- nothing to race on, no need to own the commit
        # boundary here. Caller decides when to commit, same as Slice 1/2.
        return ride_repo.create(
            rider_id=rider_id, pickup_lat=pickup_lat, pickup_lng=pickup_lng
        )

    try:
        # RideRepository.create() flushes internally, and it's the flush --
        # not necessarily db.commit() -- that can raise the IntegrityError:
        # Postgres makes a second INSERT racing on the same unique key block
        # at the row-lock level until the first transaction ends, so the
        # violation can surface here, once unblocked, rather than at the
        # commit() below. Both calls must be inside this one try block.
        ride = ride_repo.create(
            rider_id=rider_id,
            pickup_lat=pickup_lat,
            pickup_lng=pickup_lng,
            idempotency_key=idempotency_key,
        )
        db.commit()
    except IntegrityError:
        db.rollback()
        winner = ride_repo.get_by_idempotency_key(idempotency_key)
        if winner is None:
            # Some other integrity error, not the key race we expected.
            raise
        return winner

    return ride
