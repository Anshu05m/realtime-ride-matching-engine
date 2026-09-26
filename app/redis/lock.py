"""A hand-rolled Redis distributed lock: atomic acquire, TTL, unique ownership
token, atomic owner-checked release. No redlock library — the mechanism stays
small and visible on purpose (see CLAUDE.md's "do not hide important logic").

What this lock actually guarantees, and what it doesn't:
- Acquisition is atomic (a single SET NX PX command), so two callers can never
  both believe they hold the same key at the same time.
- Release only ever deletes a key this exact acquisition owns (checked via a
  server-side Lua script, not a Python-side GET-then-DEL, which would itself
  be racy).
- It does NOT guarantee the holder is still alive or still "the rightful
  owner" once the TTL elapses — a slow holder (GC pause, slow query, dead
  process) can have its lock silently expire and get acquired by someone else
  while the original holder is still working. This is a known limitation of
  pure-TTL locks with no fencing token (see the Redlock safety debate). It's
  exactly why this project does not treat the lock as the final correctness
  guarantee — see app/matching/matcher.py and the Postgres partial unique
  index it depends on.
"""

import uuid

from app.redis.client import redis_client


class LockingUnavailableError(Exception):
    """Raised (Slice 9) when Redis itself is unreachable/timing out during
    lock acquisition -- distinct from `token is None` (the key is held by
    someone else, an ordinary, expected outcome) and distinct from
    NoAvailableDriverError (no driver exists, a normal business outcome).
    This means the coordination layer itself is down, which is an
    infrastructure failure, not a matching outcome -- conflating the three
    would misattribute what's actually wrong. Defined here, next to the
    primitive, rather than in app/matching/matcher.py: the primitive stays
    the single source of truth for what "lock acquisition can fail" means,
    even though the primitive's own functions don't raise it themselves
    (see match_ride, which is where redis.exceptions.ConnectionError/
    TimeoutError actually get caught and translated into this)."""

# Atomic compare-and-delete: only removes the key if its value still matches
# the token we were given, so a caller can never release a lock it doesn't
# own (e.g. one that expired and was re-acquired by someone else). Must be a
# single server-side script -- a Python-side GET then DEL would itself have a
# TOCTOU gap between the check and the delete.
_RELEASE_SCRIPT = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("del", KEYS[1])
else
    return 0
end
"""
_release_script = redis_client.register_script(_RELEASE_SCRIPT)


def acquire_lock(key: str, ttl_ms: int) -> str | None:
    """Attempts to atomically claim `key`. Returns a unique ownership token on
    success, or None if the key is already held by someone else."""
    token = str(uuid.uuid4())
    acquired = redis_client.set(key, token, nx=True, px=ttl_ms)
    return token if acquired else None


def release_lock(key: str, token: str) -> bool:
    """Releases `key`, but only if it's still held by this exact token. Returns
    True if this call actually deleted the key, False if the key was already
    gone or held by someone else (e.g. it expired and was re-acquired)."""
    deleted = _release_script(keys=[key], args=[token])
    return bool(deleted)
