"""Slice 9: the "worker dies mid-assignment" chaos scenario CLAUDE.md's
Slice 9 bullets ask for, and that tests/integration/test_lock.py's own
docstring already named as deferred here. Turns the prose claims in
INTERVIEW_PREP.md's Slice 3 section into real, executable, passing tests.

A raised Python exception still runs `finally` -- that's normal exception
propagation, not a faithful model of a hard process kill where `finally`
never executes at all. So both scenarios below reuse the REAL match_ride()
end-to-end via targeted monkeypatches -- patching what a crash would have
prevented from happening (a successful commit, or a lock release), not
hand-copying match_ride's internal control flow (which would silently drift
if matcher.py's sequence changes later).

Requires `docker compose up -d postgres redis` running first.
"""

import time

import pytest

from app.config import settings
from app.matching import matcher
from app.matching.geo import latlng_to_h3
from app.matching.matcher import NoAvailableDriverError, match_ride
from app.models.driver import DriverStatus
from app.models.ride import RideStatus
from app.redis.client import redis_client
from app.redis.lock import acquire_lock
from app.storage.repositories.driver_repository import DriverRepository
from app.storage.repositories.ride_repository import RideRepository
from app.storage.repositories.rider_repository import RiderRepository

LAT, LNG = 37.7749, -122.4194
H3_INDEX = latlng_to_h3(LAT, LNG, settings.h3_resolution)
ZONE_ID = "zone-crash-test"


def _make_driver(session):
    return DriverRepository(session).create(
        current_lat=LAT, current_lng=LNG, h3_index=H3_INDEX, zone_id=ZONE_ID
    )


def _make_requested_ride(session):
    rider = RiderRepository(session).create(pickup_lat=LAT, pickup_lng=LNG)
    return RideRepository(session).create(rider_id=rider.id, pickup_lat=LAT, pickup_lng=LNG)


def _lock_key(driver_id) -> str:
    return f"lock:driver:{driver_id}"


def test_worker_dies_before_commit_strands_the_ride_until_the_lock_clears(
    committing_session_factory, monkeypatch
):
    setup = committing_session_factory()
    driver = _make_driver(setup)
    ride = _make_requested_ride(setup)
    setup.commit()
    setup.close()

    crashed_session = committing_session_factory()
    crashed_ride = RideRepository(crashed_session).get_by_id(ride.id)

    def _dead_commit():
        raise RuntimeError("simulated worker crash: process died mid-commit")

    # ride_repo.transition_status()'s UPDATE has already been sent
    # (uncommitted) by the time match_ride calls db.commit() -- patching
    # commit itself, not some earlier step, models "died exactly here."
    monkeypatch.setattr(crashed_session, "commit", _dead_commit)
    # A hard-killed process never reaches `finally`'s release_lock() call --
    # a no-op models that faithfully (a raised exception would still run a
    # REAL finally block, which isn't what a process kill does).
    monkeypatch.setattr(matcher, "release_lock", lambda *a, **k: False)

    with pytest.raises(RuntimeError):
        match_ride(crashed_session, crashed_ride)

    # The lock is genuinely still held -- release_lock was a no-op, exactly
    # as it would be if the crashed process never reached its `finally`.
    assert acquire_lock(_lock_key(driver.id), ttl_ms=100) is None

    # Explicit, not implicit: models "the DB/OS eventually notices the dead
    # connection" as its own distinct step, separate from "did finally run."
    crashed_session.rollback()
    crashed_session.close()

    # Nothing durable happened: reread from a FRESH session. The driver's
    # `driver.status = BUSY` mutation was a pure in-memory attribute change
    # (SessionLocal/committing_session_factory both set autoflush=False) --
    # it was never even sent to Postgres, let alone committed.
    fresh = committing_session_factory()
    reread_ride = RideRepository(fresh).get_by_id(ride.id)
    reread_driver = DriverRepository(fresh).get_by_id(driver.id)
    assert reread_ride.status == RideStatus.REQUESTED
    assert reread_driver.status == DriverStatus.AVAILABLE
    fresh.close()

    # Recovery, proven directly: once the lock clears, a fresh ride's
    # match_ride() call succeeds against the same driver. The TTL-expiry
    # mechanism ITSELF is already proven in isolation by
    # test_lock.py::test_ttl_expiry_frees_the_lock_without_explicit_release
    # -- deleting the key here models "it has since cleared" without
    # re-proving Redis's own EXPIRE semantics a second time.
    redis_client.delete(_lock_key(driver.id))

    recovery_session = committing_session_factory()
    new_ride = _make_requested_ride(recovery_session)
    recovery_session.commit()
    matched = match_ride(recovery_session, RideRepository(recovery_session).get_by_id(new_ride.id))
    assert matched.status == RideStatus.MATCHED
    assert matched.driver_id == driver.id
    recovery_session.close()

    # The ORIGINAL crashed ride stays stranded forever -- no automatic
    # retry. A real, named limitation (INTERVIEW_PREP.md, Slice 3), now
    # actually checked, not just claimed in prose.
    final = committing_session_factory()
    still_stranded = RideRepository(final).get_by_id(ride.id)
    assert still_stranded.status == RideStatus.REQUESTED
    final.close()


def test_worker_dies_before_commit_recovers_via_a_real_ttl_expiry(
    committing_session_factory, monkeypatch
):
    """Same scenario as above, but proven with a real (short) TTL sleep
    through match_ride itself, rather than directly deleting the lock key --
    un-cheated, genuine end-to-end proof that the TTL mechanism recovers
    this specific system-level scenario, not just the isolated primitive.
    monkeypatch.setattr on `settings` is safe here (unlike a bare global
    mutation) -- pytest guarantees it reverts even if this test fails."""
    monkeypatch.setattr(settings, "lock_ttl_ms", 200)

    setup = committing_session_factory()
    driver = _make_driver(setup)
    ride = _make_requested_ride(setup)
    setup.commit()
    setup.close()

    crashed_session = committing_session_factory()
    crashed_ride = RideRepository(crashed_session).get_by_id(ride.id)

    def _dead_commit():
        raise RuntimeError("simulated worker crash")

    monkeypatch.setattr(crashed_session, "commit", _dead_commit)
    monkeypatch.setattr(matcher, "release_lock", lambda *a, **k: False)

    with pytest.raises(RuntimeError):
        match_ride(crashed_session, crashed_ride)
    crashed_session.rollback()
    crashed_session.close()

    time.sleep(0.3)  # real TTL expiry, not simulated

    recovery_session = committing_session_factory()
    new_ride = _make_requested_ride(recovery_session)
    recovery_session.commit()
    matched = match_ride(recovery_session, RideRepository(recovery_session).get_by_id(new_ride.id))
    assert matched.status == RideStatus.MATCHED
    assert matched.driver_id == driver.id
    recovery_session.close()


def test_worker_dies_after_commit_is_benign(committing_session_factory, monkeypatch):
    setup = committing_session_factory()
    driver = _make_driver(setup)
    ride = _make_requested_ride(setup)
    setup.commit()
    setup.close()

    session = committing_session_factory()
    live_ride = RideRepository(session).get_by_id(ride.id)
    # Models "crashed right after commit, before finally's release_lock ran."
    monkeypatch.setattr(matcher, "release_lock", lambda *a, **k: False)

    matched = match_ride(session, live_ride)

    assert matched.status == RideStatus.MATCHED
    assert matched.driver_id == driver.id

    fresh = committing_session_factory()
    reread_driver = DriverRepository(fresh).get_by_id(driver.id)
    assert reread_driver.status == DriverStatus.BUSY  # durably correct despite the "crash"
    fresh.close()

    # The orphaned lock key is still present in Redis...
    assert acquire_lock(_lock_key(driver.id), ttl_ms=100) is None

    # ...but provably harmless: a second ride's candidate discovery never
    # even considers this driver, since it's BUSY -- not merely unexercised
    # by coincidence in this one test.
    second_session = committing_session_factory()
    second_ride = _make_requested_ride(second_session)
    second_session.commit()
    with pytest.raises(NoAvailableDriverError):
        match_ride(second_session, RideRepository(second_session).get_by_id(second_ride.id))
    second_session.close()

    session.close()
