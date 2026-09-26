"""Idempotent, surge-priced ride creation (Slices 4-5).

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

Transaction boundary note: create_ride commits its own successful insert
UNCONDITIONALLY, whether or not an idempotency key was given -- a deliberate
exception to this codebase's normal "caller owns commit" default (see
app/matching/matcher.py for the first such exception, and why this is a
separate instance of the same *shape* of constraint, not the same reason).
When a key IS given: two callers racing on the same key have Postgres block
the losing INSERT at the row-lock level until the winning transaction
actually ends (commit or rollback) -- not merely flushes -- so owning the
commit is what deterministically unblocks a concurrent racer instead of
leaving it blocked on whatever unrelated work a caller does before it gets
around to committing. When no key is given, there's no race to resolve, but
this function still commits unconditionally (Slice 6 change): the FastAPI
`get_db()` dependency never auto-commits, so a keyless POST /rides would
otherwise silently lose its created ride the moment the request ends and
the session closes -- a real bug the original Slice 4 design didn't need to
consider before a non-test caller existed. Fixing it here, once, is better
than requiring every future caller (this API layer, Slice 7's simulation
engine, ...) to remember to commit defensively after calling this function.

Slice 5 note: create_ride also quotes the ride's zone and surge multiplier
at request time, computed BEFORE the new Ride row is written -- under READ
COMMITTED a transaction sees its own uncommitted writes, so computing after
the insert would let a ride count itself in its own demand snapshot. Surge
is quoted here rather than later at match time because matching is a
separate, optional step (a ride can stay REQUESTED forever if
NoAvailableDriverError is raised) -- quoting only on a successful match
would leave every unmatched ride with no zone/price at all.

Slice 8 note: create_ride emits a RIDE_REQUESTED dashboard event, but only
on a genuine new create -- not on an idempotent-replay hit (the early
`return existing` above never reaches the emit call), so a retried request
doesn't log a second, misleading "requested" event for the same ride.
cancel_ride/complete_ride emit RIDE_CANCELLED/RIDE_COMPLETED; these are also
this module's first logger calls at all (added as a direct side effect of
wiring the dashboard's event log, via app.observability.events.emit, not a
gap that existed before).
"""

import uuid
from collections.abc import Iterable

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings
from app.models.driver import DriverStatus
from app.models.ride import InvalidRideStateError, Ride, RideNotFoundError, RideStatus
from app.observability.events import emit
from app.pricing.surge import zone_surge
from app.pricing.zones import zone_id_for_point
from app.storage.repositories.driver_repository import DriverRepository
from app.storage.repositories.ride_repository import ACTIVE_RIDE_STATUSES, RideRepository

# Complete is valid only from a status that actually has a driver attached.
# REQUESTED is deliberately excluded -- you can't complete a driverless ride.
# IN_PROGRESS has no code path that sets it anywhere in this project yet (no
# "start trip" endpoint exists in CLAUDE.md's spec), but it's kept here as
# harmless forward-compatibility, not dead code to justify.
_COMPLETABLE_STATUSES = (RideStatus.MATCHED, RideStatus.IN_PROGRESS)


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

    # Quote zone + surge now, before any Ride row is written -- computing
    # after the insert would let this ride count itself in its own demand
    # snapshot (READ COMMITTED sees a transaction's own uncommitted writes).
    zone_id = zone_id_for_point(pickup_lat, pickup_lng, settings.surge_zone_resolution)
    surge = zone_surge(db, zone_id)

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
            zone_id=zone_id,
            surge_multiplier=surge.multiplier,
        )
        db.commit()
    except IntegrityError:
        db.rollback()
        if idempotency_key is None:
            # No key means no legitimate uniqueness race was possible here --
            # this is some other integrity error, not one to swallow. (Also
            # avoids get_by_idempotency_key(None), which SQLAlchemy would
            # translate to "WHERE idempotency_key IS NULL" and could silently
            # return an unrelated null-key ride instead of re-raising.)
            raise
        winner = ride_repo.get_by_idempotency_key(idempotency_key)
        if winner is None:
            # Some other integrity error, not the key race we expected.
            raise
        return winner

    emit(
        "RIDE_REQUESTED",
        f"ride {ride.id} requested by rider {rider_id}",
        ride_id=str(ride.id),
        rider_id=str(rider_id),
        zone_id=zone_id,
        surge_multiplier=surge.multiplier,
        pickup_lat=pickup_lat,
        pickup_lng=pickup_lng,
    )
    return ride


def _transition_or_raise(
    db: Session,
    ride_repo: RideRepository,
    ride_id: uuid.UUID,
    *,
    from_statuses: Iterable[RideStatus],
    to_status: RideStatus,
) -> Ride:
    """Shared body of cancel_ride/complete_ride: fetch, atomically transition,
    and raise the right exception with an honest, freshly-read status on
    failure. See app/matching/matcher.py's module docstring and
    RideRepository.transition_status for the concurrency design this is
    part of -- match_ride, cancel_ride, and complete_ride all use the same
    atomic-conditional-UPDATE mechanism rather than three separate ones."""
    ride = ride_repo.get_by_id(ride_id)
    if ride is None:
        raise RideNotFoundError(f"ride {ride_id} not found")

    transitioned = ride_repo.transition_status(
        ride_id, from_statuses=from_statuses, to_status=to_status
    )
    if not transitioned:
        db.rollback()
        current = ride_repo.get_by_id(ride_id)
        current_status = current.status.value if current is not None else "unknown"
        raise InvalidRideStateError(
            f"ride {ride_id} cannot transition to {to_status.value} "
            f"from its current status ({current_status})"
        )

    # Refresh BEFORE checking driver_id, not after: the initially-fetched
    # `ride` object's driver_id can itself be stale if a concurrent
    # match_ride assigned a driver between our fetch and our own conditional
    # UPDATE above (our UPDATE only touches `status`, so it still succeeds
    # against a since-MATCHED row -- that's correct, matched rides are a
    # valid cancel source -- but it means the driver to free might not be the
    # one, or might not exist yet, in our stale copy).
    db.refresh(ride)
    return ride


def cancel_ride(db: Session, ride_id: uuid.UUID) -> Ride:
    ride_repo = RideRepository(db)
    ride = _transition_or_raise(
        db, ride_repo, ride_id, from_statuses=ACTIVE_RIDE_STATUSES, to_status=RideStatus.CANCELLED
    )

    if ride.driver_id is not None:
        driver = DriverRepository(db).get_by_id(ride.driver_id, fresh=True)
        if driver is not None:
            driver.status = DriverStatus.AVAILABLE

    db.commit()
    db.refresh(ride)
    emit(
        "RIDE_CANCELLED",
        f"ride {ride.id} cancelled",
        ride_id=str(ride.id),
        driver_id=str(ride.driver_id) if ride.driver_id else None,
    )
    return ride


def complete_ride(db: Session, ride_id: uuid.UUID) -> Ride:
    ride_repo = RideRepository(db)
    ride = _transition_or_raise(
        db,
        ride_repo,
        ride_id,
        from_statuses=_COMPLETABLE_STATUSES,
        to_status=RideStatus.COMPLETED,
    )

    if ride.driver_id is not None:
        driver = DriverRepository(db).get_by_id(ride.driver_id, fresh=True)
        if driver is not None:
            driver.status = DriverStatus.AVAILABLE

    db.commit()
    db.refresh(ride)
    emit(
        "RIDE_COMPLETED",
        f"ride {ride.id} completed",
        ride_id=str(ride.id),
        driver_id=str(ride.driver_id) if ride.driver_id else None,
    )
    return ride
