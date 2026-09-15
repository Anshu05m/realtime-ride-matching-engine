"""Slice 2's matching algorithm: candidate discovery + ranking + a best-effort
assignment. Deliberately has NO distributed locking yet (that's Slice 3).

Concurrency note: `match_ride` re-checks a candidate driver's status immediately
before assigning it, which catches the case where a driver went BUSY sometime
after the initial candidate query. It does NOT make this function safe under
real concurrency: two callers can both pass that re-check for the same driver
before either has written BUSY back (a classic check-then-act / TOCTOU race).
Nothing in this slice serializes that critical section, and no database
constraint yet rejects a second assignment to the same driver. Slice 3 closes
this gap with a Redis lock around the critical section plus a Postgres
partial-unique-index as the durable backstop.

The ride and driver mutations below share a single flush() in one session, so a
process crash before the caller commits loses both together — never "driver
marked BUSY with no ride matched."
"""

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.matching.candidate_search import find_ranked_candidates
from app.models.driver import DriverStatus
from app.models.ride import Ride, RideStatus
from app.storage.repositories.driver_repository import DriverRepository


class NoAvailableDriverError(Exception):
    """Raised when no candidate driver survives ranking and re-check. The ride
    and every candidate driver are left untouched — the caller may retry later
    or surface a clean failure to the rider."""


def match_ride(db: Session, ride: Ride) -> Ride:
    if ride.status != RideStatus.REQUESTED:
        raise ValueError(f"ride {ride.id} is not in REQUESTED status (got {ride.status})")

    ranked = find_ranked_candidates(db, ride.pickup_lat, ride.pickup_lng)
    driver_repo = DriverRepository(db)

    for candidate, _distance_km in ranked:
        # Re-check immediately before assigning: best-effort only, see module
        # docstring for what this does and does not protect against.
        driver = driver_repo.get_by_id(candidate.id)
        if driver is None or driver.status != DriverStatus.AVAILABLE:
            continue

        driver.status = DriverStatus.BUSY
        ride.driver_id = driver.id
        ride.status = RideStatus.MATCHED
        ride.matched_at = datetime.now(UTC)
        db.flush()
        return ride

    raise NoAvailableDriverError(f"no available driver found for ride {ride.id}")
