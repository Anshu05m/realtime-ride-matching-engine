"""Slice 3's matching algorithm: candidate discovery + ranking (Slice 2, unlocked
and shared/read-only) + a Redis-locked, Postgres-backed assignment.

Concurrency design, in order of what actually stops two requests from
double-booking the same driver:

1. A Redis lock (`lock:driver:{id}`, see app/redis/lock.py) serializes
   assignment attempts per driver. Only one caller can hold a given driver's
   lock at a time, so the "re-check availability, then write" sequence below
   can never interleave with another caller doing the same thing to the same
   driver. This is *coordination*: it makes contention cheap to resolve
   instead of racing all the way to the database.

2. The lock is held across the database COMMIT, not just the in-memory
   check-and-mutate. This matters: if the lock were released before commit, a
   second caller could acquire it and re-check the driver while still seeing
   the pre-assignment state under READ COMMITTED isolation (an uncommitted
   write isn't visible to other transactions), reopening the exact race the
   lock exists to close. So "commit, then release" is not stylistic — it's
   the actual correctness argument for why holding the lock helps at all.

3. A Postgres partial unique index (`ux_rides_one_active_ride_per_driver`,
   see the Slice 3 migration) is the actual durable correctness boundary: at
   most one row in `rides` can have a given driver_id while status is active.
   This holds true independent of whether the Redis lock worked correctly —
   if it's ever violated despite the lock (a lock bug, a bypass, Redis being
   skipped), the commit below fails with IntegrityError and is treated like
   any other lost race: roll back, release the lock, try the next candidate.

What this does NOT guarantee: if a worker dies while holding the lock but
before committing, nothing here automatically retries the stranded
REQUESTED ride once the lock's TTL expires — see INTERVIEW_PREP.md for the
full worker-death-timing writeup. This is not "exactly once" or "fault
tolerant" matching; it's "no double-booking survives durably," which is a
narrower, defensible claim.
"""

import logging
from datetime import UTC, datetime

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings
from app.matching.candidate_search import find_ranked_candidates
from app.models.driver import DriverStatus
from app.models.ride import Ride, RideStatus
from app.redis.lock import acquire_lock, release_lock
from app.storage.repositories.driver_repository import DriverRepository

logger = logging.getLogger(__name__)


class NoAvailableDriverError(Exception):
    """Raised when no candidate driver survives ranking, locking, and
    re-check. The ride and every candidate driver are left untouched — the
    caller may retry later or surface a clean failure to the rider."""


def _lock_key(driver_id) -> str:
    # Deliberately namespaced (not the bare "driver:123" from CLAUDE.md's
    # example) so lock keys are visually distinct from any future cached
    # driver state that might live under a plain "driver:" prefix.
    return f"lock:driver:{driver_id}"


def match_ride(db: Session, ride: Ride) -> Ride:
    if ride.status != RideStatus.REQUESTED:
        raise ValueError(f"ride {ride.id} is not in REQUESTED status (got {ride.status})")

    # Candidate discovery + ranking is read-only and shared — no lock needed
    # (and holding one across an H3 ring search would only add contention for
    # no benefit; CLAUDE.md's flow explicitly ranks before locking).
    ranked = find_ranked_candidates(db, ride.pickup_lat, ride.pickup_lng)
    driver_repo = DriverRepository(db)

    for candidate, _distance_km in ranked:
        lock_key = _lock_key(candidate.id)
        token = acquire_lock(lock_key, settings.lock_ttl_ms)
        if token is None:
            logger.info("skipped candidate %s: lock contention", candidate.id)
            continue

        try:
            # Re-check under the lock: no concurrent writer can interleave with
            # this driver while we hold it, so this check is now meaningful
            # (unlike Slice 2's best-effort version) -- but only if it actually
            # reads current data. fresh=True forces a real SELECT instead of a
            # cached row from this session's identity map (candidate discovery,
            # above, already loaded this same driver earlier in this session;
            # without `fresh`, get_by_id would silently return that stale
            # in-memory copy instead of re-querying).
            driver = driver_repo.get_by_id(candidate.id, fresh=True)
            if driver is None or driver.status != DriverStatus.AVAILABLE:
                logger.info("skipped candidate %s: failed re-check", candidate.id)
                continue

            driver.status = DriverStatus.BUSY
            ride.driver_id = driver.id
            ride.status = RideStatus.MATCHED
            ride.matched_at = datetime.now(UTC)

            try:
                db.commit()
            except IntegrityError:
                # The Postgres backstop rejected this despite the lock (should
                # only happen if lock coordination was bypassed or buggy).
                # Treat it exactly like a lost race: roll back and move on.
                db.rollback()
                logger.warning(
                    "candidate %s failed the active-ride constraint despite holding the lock",
                    candidate.id,
                )
                continue

            return ride
        finally:
            # Always release, on every path (success, failed re-check, or a
            # rolled-back commit) — and only ever this lock, via its token.
            release_lock(lock_key, token)

    raise NoAvailableDriverError(f"no available driver found for ride {ride.id}")
