"""Tests for the Redis lock primitive itself (app/redis/lock.py), not a
simulated worker-crash scenario -- see BUGS_AND_ISSUES.md / INTERVIEW_PREP.md
Slice 3 for why the full "worker dies mid-assignment" chaos test is deferred
to Slice 9. This just proves the primitive's contract holds: atomic
acquisition, owner-only release, and TTL expiry actually frees the key.

Requires `docker compose up -d postgres redis` running first.
"""

import time
import uuid

from app.redis.lock import acquire_lock, release_lock


def _unique_key() -> str:
    # Each test uses its own key so tests never interfere with each other,
    # without needing any Redis-side cleanup between tests.
    return f"lock:test:{uuid.uuid4()}"


def test_acquire_returns_a_token():
    key = _unique_key()
    token = acquire_lock(key, ttl_ms=5000)
    assert token is not None
    release_lock(key, token)


def test_second_acquire_fails_while_first_holds_it():
    key = _unique_key()
    first_token = acquire_lock(key, ttl_ms=5000)
    second_token = acquire_lock(key, ttl_ms=5000)

    assert first_token is not None
    assert second_token is None

    release_lock(key, first_token)


def test_release_frees_the_key_for_a_new_acquire():
    key = _unique_key()
    token = acquire_lock(key, ttl_ms=5000)
    release_lock(key, token)

    new_token = acquire_lock(key, ttl_ms=5000)
    assert new_token is not None
    release_lock(key, new_token)


def test_release_with_wrong_token_does_not_delete_the_lock():
    key = _unique_key()
    real_token = acquire_lock(key, ttl_ms=5000)

    released = release_lock(key, "not-the-real-token")

    assert released is False
    # The real owner should still be able to release its own lock afterward --
    # proof the wrong-token release didn't touch it.
    assert release_lock(key, real_token) is True


def test_release_returns_false_for_an_already_gone_key():
    key = _unique_key()
    assert release_lock(key, "any-token") is False


def test_ttl_expiry_frees_the_lock_without_explicit_release():
    # This is the mechanism behind Slice 3's resilience story: a worker that
    # dies while holding the lock never releases it, but the lock still
    # clears on its own once the TTL elapses, so a driver can't be stranded
    # forever. A short TTL here stands in for "a worker crashed."
    key = _unique_key()
    first_token = acquire_lock(key, ttl_ms=200)
    assert first_token is not None

    # Immediately after acquiring, the lock is still held.
    assert acquire_lock(key, ttl_ms=200) is None

    time.sleep(0.3)

    second_token = acquire_lock(key, ttl_ms=5000)
    assert second_token is not None
    release_lock(key, second_token)
