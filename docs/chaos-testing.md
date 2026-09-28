# Chaos / Failure Testing

> Part of the [Real-Time Ride-Matching Engine](../README.md). This is the full
> write-up of the failure scenarios summarized in the README. Every scenario is
> an executable test under `tests/failure/`
> (`./venv/bin/pytest tests/failure/ -v`). References below to
> `INTERVIEW_PREP.md` and `docs/plans/` are maintainer-side notes that are not
> part of the published repository.

Four reproducible failure scenarios (`tests/failure/`, run via
`./venv/bin/pytest tests/failure/ -v`), each turning a claim that used to
live only in prose (`INTERVIEW_PREP.md`) into an executable, passing test.
Two real bugs were found and fixed while writing these: a generic database
error and a Redis outage both used to crash as an unhandled, unstructured
500 — contradicting this API's own stated promise that every error comes
back as a structured `{"error": {"code", "message"}}` envelope. Both are
now honest, structured `503`s.

## Scenario 1 — Worker dies holding the Redis lock, before the commit

*What failed*: the process crashes (simulated by making `commit()` raise and
`release_lock()` a no-op — a raised Python exception still runs `finally`,
so only a no-op faithfully models a hard kill) after acquiring the driver's
Redis lock and mutating in-memory state, but before the transaction commits.

*State before failure*: a `REQUESTED` ride, an `AVAILABLE` driver, the Redis
lock held by the process that's about to "die."

*Did the DB transaction commit?* No. The ride's `transition_status` UPDATE
had already been sent (uncommitted) to Postgres; the driver's `BUSY`
mutation was a pure in-memory attribute change, never even flushed
(`autoflush=False`). Both are discarded once the abandoned connection is
rolled back.

*What happened to the Redis lock?* Not released — held until its TTL
(`lock_ttl_ms`, default 5000ms) elapses on its own.

*What recovered automatically?* The driver. Once the TTL clears, the lock
key expires and becomes acquirable again — proven two ways: directly
clearing the key (the TTL-expiry mechanism itself is already proven in
isolation by `test_lock.py`) and, separately, with a real ~200ms TTL and an
actual sleep, for genuine un-cheated proof through `match_ride` itself. A
fresh ride's match attempt against the same driver succeeds immediately
after.

*What required retry?* The **original** crashed ride does not recover on
its own — it stays at `REQUESTED` forever. Nothing in this system
automatically retries a stranded ride. This is a real, open, named gap, not
glossed over.

*Was any ride lost?* No — the row still exists, durably, in `REQUESTED`
status. Stranded, not corrupted or deleted.

*Was duplicate assignment possible?* No — nothing was ever committed, so
the driver was never durably assigned. Once the lock clears it's correctly
available to any ride, including a client-retried resubmission of the
original one.

*Guarantees*: no double-booking ever survives durably; a driver is never
*permanently* stranded by a crash. **Not** guaranteed: the ride itself does
not self-heal, and this is **not** "exactly-once" or "fault-tolerant"
matching.

## Scenario 2 — Worker dies after the commit, before releasing the lock

*What failed*: the process crashes after a successful commit but before
`release_lock()` runs (`release_lock` patched to a no-op; the commit itself
is real).

*Did the DB transaction commit?* Yes — the ride is durably `MATCHED`, the
driver durably `BUSY`.

*What happened to the Redis lock?* Orphaned — the key remains in Redis
until its TTL elapses.

*What recovered automatically?* Nothing needed to — this state is already
fully correct. The orphaned lock is inert, proven directly rather than
merely asserted: a second ride's real match attempt against the same driver
is shown to raise `NoAvailableDriverError`, because candidate discovery
only ever considers `AVAILABLE` drivers — this one is `BUSY`, so nothing
would ever try to acquire that specific lock again regardless of whether it
expired yet.

*Was any ride lost? Was duplicate assignment possible?* No, and no.

*Guarantees*: zero negative consequences from this scenario — the only
cost is a few seconds of an inert, unused Redis key.

## Scenario 3 — Database failure during commit

*What failed*: a generic database error (e.g. a dropped connection, or
`sqlalchemy.exc.TimeoutError` from connection-pool exhaustion — a real
failure mode, not just a hypothetical, given this deployment's default
15-connection pool; see [benchmarks.md](benchmarks.md)) during a commit.

*Found while testing, then fixed*: this previously propagated as an
unhandled, unstructured 500. Now caught by a `SQLAlchemyError` handler
(deliberately the *base* class, not one specific subtype) that returns a
structured `503` with a **fixed, generic** message
(`{"code": "database_error", "message": "a database error occurred, please
retry"}`) — never the raw driver exception, which could leak internal
detail (SQL fragments, connection info). The real exception is logged
server-side via `logger.exception(...)`.

*Did the DB transaction commit? What happened to the Redis lock?* No commit
succeeded for the failing operation. If the failure occurs inside
`match_ride`, the lock **is** released normally — this is a real, in-process
Python exception (not a hard crash), so `finally` runs as designed.

*What required retry?* The client. A `503` is an explicit "safe to retry"
signal; retried ride-creation requests using the same `Idempotency-Key`
inherit Slice 4's duplicate-prevention guarantee even across retries.

*Was any ride lost? Was duplicate assignment possible?* No, and no — nothing
was durably mutated by the failed attempt.

*Guarantees*: every database failure now surfaces honestly and safely-
retryably. **Not** guaranteed: automatic server-side retry — that
responsibility stays with the caller, consistent with the rest of this
API's idempotency-key-driven design.

## Scenario 4 — Redis unavailable during lock acquisition

*What failed*: Redis becomes unreachable (`ConnectionError`/`TimeoutError`)
exactly when `match_ride` tries to acquire a candidate's lock.

*Found while testing, then fixed*: this also previously propagated as an
unhandled 500. Now caught **narrowly** — `ConnectionError`/`TimeoutError`
only, deliberately *not* the broad `RedisError` (which also covers things
like a malformed command, an actual application bug that has no business
being reclassified as "infrastructure is down") — and raised immediately
as a new `LockingUnavailableError` rather than looping through the
remaining candidates, which would all fail identically. Verified directly:
a simulated `ResponseError` (the "actual bug" case) is proven to **not** be
caught by this handler — it propagates as itself, a known, deliberately
un-softened gap, not something silently misattributed to an outage.

*Did the DB transaction commit? What happened to the "lock"?* Nothing —
the failure happens before any write is attempted and before any lock is
actually held, so there's nothing to release or leak.

*What recovered automatically?* The client gets an honest, structured `503`
(`{"code": "locking_unavailable", "message": "the matching coordination
layer is temporarily unavailable"}`) instead of a crash, immediately rather
than after wastefully trying every candidate.

*Was any ride lost? Was duplicate assignment possible?* No, and no — the
ride is untouched, still `REQUESTED`.

*Guarantees*: a Redis outage is reported honestly, distinctly from "no
driver available" (a normal business outcome) and from an actual bug.
**Not** guaranteed: retries, circuit-breaking, or Redis failover — a
sustained outage means matching is fully unavailable for as long as it
lasts (read-only endpoints that never touch Redis are unaffected).
