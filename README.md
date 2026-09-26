# Real-Time Ride-Matching Engine

A real-time ride-matching backend that matches ride requests to nearby available
drivers, with correctness guarantees under concurrency, dynamic surge pricing, and
a live visual dashboard.

The domain is ride-hailing, but the underlying pattern — matching a scarce resource
to a requester under concurrent access while maintaining correctness — generalizes
to delivery dispatch, inventory allocation, seat/ticket booking, payment processing,
and other distributed systems.

This is a portfolio project, not a production-scale system: the goal is a focused,
correctly engineered, well-documented, demo-able system that proves specific
backend/distributed-systems engineering skills, not feature parity with Uber.

Full architecture, design decisions, and benchmark results land in later slices
(see the build plan below) and will be documented here as they're built.

## Requirements

- Python 3.12+
- Docker + Docker Compose

## Setup

```bash
python3.12 -m venv venv
./venv/bin/pip install -e ".[dev]"

cp .env.example .env

docker compose up -d postgres redis   # or `docker compose up` to include the app
./venv/bin/alembic upgrade head       # only needed if not using the app container,
                                       # which runs this automatically on startup
```

## Running the app

```bash
docker compose up            # postgres + redis + app, migrations run automatically
curl http://localhost:8000/health
```

## Running tests

Tests require Postgres and Redis running (`docker compose up -d postgres redis`).
Integration tests use a dedicated `ride_matching_test` database, provisioned
automatically the first time the postgres container starts.

```bash
./venv/bin/pytest -v
```

Most tests only need `postgres`/`redis`. One test —
`tests/concurrency/test_simulation_contention.py` — additionally requires the
full stack (`docker compose up`, including the `app` container) and is
skipped automatically if nothing answers on `localhost:8000`.

## Running the simulation

With the full stack up (`docker compose up`), generate synthetic traffic
against the live API:

```bash
./venv/bin/python -m app.simulation --duration-seconds 30
```

This creates a pool of synthetic drivers, moves them around a simulated area,
and fires ride requests — including deliberately concentrated,
barrier-synchronized bursts aimed at a small "hotspot" sub-region — to
exercise real driver-matching contention end-to-end. Prints a summary
(match rate, client-measured p50/p95/p99 latency) at the end. See
`./venv/bin/python -m app.simulation --help` for every configurable
parameter (driver/rider counts, request rate, area/hotspot geometry, driver
speed, ride duration, cancellation rate, a `--seed` for reproducible runs).

## Live dashboard

With the full stack up (`docker compose up`), open
[http://localhost:8000/dashboard/](http://localhost:8000/dashboard/) for a
live Leaflet map of drivers, a stats/surge panel, and a live event log fed
by a WebSocket (`GET /ws`) — run the simulation (above) in another shell to
watch matching activity, including real lock contention, as it happens.

The map/stats panel are driven by periodic REST polling (`GET /drivers`,
`GET /stats`); the WebSocket is used only as an append-only stream for the
event log and transient map animations — see `docs/plans/slice-08-
dashboard.md` for why.

## Chaos / Failure Testing

Four reproducible failure scenarios (`tests/failure/`, run via
`./venv/bin/pytest tests/failure/ -v`), each turning a claim that used to
live only in prose (`INTERVIEW_PREP.md`) into an executable, passing test.
Two real bugs were found and fixed while writing these: a generic database
error and a Redis outage both used to crash as an unhandled, unstructured
500 — contradicting this API's own stated promise that every error comes
back as a structured `{"error": {"code", "message"}}` envelope. Both are
now honest, structured `503`s.

### Scenario 1 — Worker dies holding the Redis lock, before the commit

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

### Scenario 2 — Worker dies after the commit, before releasing the lock

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

### Scenario 3 — Database failure during commit

*What failed*: a generic database error (e.g. a dropped connection, or
`sqlalchemy.exc.TimeoutError` from connection-pool exhaustion — a real
failure mode, not just a hypothetical, given this deployment's default
15-connection pool; see "Load Testing" below) during a commit.

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

### Scenario 4 — Redis unavailable during lock acquisition

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

## Load Testing

Run via `./venv/bin/pip install -e ".[load-test]"` then
`./load_tests/run_benchmarks.sh` — see that script and
`load_tests/locustfile.py` for the full protocol. Every number below is
from an actual run (raw CSV/JSON evidence committed under
`load_tests/results/`) — none are estimated.

**Environment**: MacBook Air, Apple M1, 8 cores / 8GB RAM, macOS 26.2,
Docker 29.7.2, Python 3.12.8, Locust 2.46.6. Benchmark target: the
Dockerfile's plain `uvicorn app.main:app` command (no `--reload`,
no `--workers`), Postgres 16 + Redis 7 in separate containers, all on one
machine — not dedicated benchmark hardware, and not representative of a
production deployment on separate hosts.

**Configuration**: 4 tiers, each a separate 60-second headless Locust run
against a freshly truncated-and-reseeded 50-driver pool (deliberately
undersized — see `docs/plans/slice-09-load-chaos-testing.md` for why). Each
simulated user repeatedly creates a rider near a fixed 2km-radius area then
requests a ride, with near-zero wait time between iterations.

**Predicted before running** (from this deployment's own, unmodified
configuration): the DB connection pool (`pool_size=5 + max_overflow=10 =
15`) and Starlette's 40-thread sync-route pool cap real concurrency well
below "500" or "1000 users." 15 concurrent users was chosen as an
unsaturated baseline specifically to sit at that ceiling.

| Tier | Users | Total requests | Failures | Median | p95 | p99 | Throughput (req/s) |
|---|---|---|---|---|---|---|---|
| Baseline | 15 | 13,977 | 0 (0.0%) | 30ms | 89ms | 140ms | 236.3 |
| Tier 2 | 100 | 772 | 21 (2.7%) | 120ms | 30,000ms | 30,000ms | 23.0 |
| Tier 3 | 500 | 307 | 25 (8.1%) | 180ms | 30,000ms | 30,000ms | 9.7 |
| Tier 4 | 1000 | 76 | 37 (48.7%) | 30,000ms | 30,000ms | 30,000ms | 2.5 |

(Locust's own client-observed numbers, aggregated across both endpoints —
see `load_tests/results/0{1,2,3,4}_*_stats.csv` for the full per-endpoint
breakdown.)

**The prediction held, exactly, and more dramatically than expected.** The
p95/p99 columns aren't approximate — they're pinned to precisely 30,000ms
starting at tier 2, which is SQLAlchemy's default `pool_timeout` (30s) being
hit directly: once all 15 connections are checked out, further requests
queue for a connection, wait the full 30 seconds, then fail. This is
Slice 9's own `database_error` fix (see "Chaos / Failure Testing" above)
firing for real, not hypothetically — tier 4's 37 failures were genuine
`503 {"code": "database_error"}` responses returned end-to-end through that
handler, confirmed directly against the live server's error log.

**An honest finding, investigated to a confirmed root cause (not left as a
guess)**: after the 1000-user tier's 60-second run ended, the server did
not immediately recover — it remained unresponsive, including to the
trivial `GET /health` endpoint, for several minutes with the container's
CPU sitting near idle. The first version of this report claimed it "never
self-recovered" and required a manual restart — that claim was premature,
made after giving up too early, and has since been corrected by a follow-up
investigation using a live Python thread dump (`py-spy dump`) and direct
Postgres session inspection (`pg_stat_activity`) rather than further
guessing:

- Every one of the 40 threads in Starlette's sync-route thread pool
  (confirmed directly: `anyio.to_thread.current_default_thread_limiter().
  total_tokens == 40`) was blocked inside SQLAlchemy's connection-pool
  checkout (`pool._do_get`) — including `/health`'s own thread, which needs
  a connection for its own liveness check and so queues exactly like any
  other request. **None** were stuck mid-transaction, mid-request, or in
  any kind of deadlock — the thread dump showed zero threads holding a
  resource while waiting on another thread that held a resource it needed.
- uvicorn is started with no `--limit-concurrency` (confirmed via `uvicorn
  --help`: unset means unlimited — no admission control), so it accepts far
  more connections during a burst than the app can promptly process; those
  connections queue, in order, behind the 40-thread pool, which itself
  queues behind the 15-connection database pool (`pool_timeout=30s`).
  Each wave of ~25 threads beyond the 15 that get a connection waits the
  full 30 seconds, times out (now a clean `503 database_error`, not a raw
  crash — this is Slice 9's own fix from "Chaos / Failure Testing" firing
  under real load), and frees its slot for the next queued request. This
  repeats until the backlog is empty.
- **This is a bounded, self-draining FIFO queue, not a deadlock** — every
  wait has a hard 30-second cap and no thread depends on another thread
  circularly, so termination is guaranteed, not merely likely. Reproduced
  directly to confirm this empirically, not just in theory: a smaller,
  controlled burst (300 concurrent users, 20 seconds) produced the identical
  symptom (`/health` timing out) and was watched, continuously, to a full,
  stable recovery — 3 minutes 28 seconds after the backlog began, Postgres
  connections returned to a normal idle state and `/health` answered in
  ~80ms consistently across 10+ follow-up checks.
- The original 1000-user run almost certainly would have recovered the same
  way had it been given enough time — it generated a substantially larger
  backlog than the 300-user reproduction, so a proportionally longer drain
  was expected, not a hang. This was not re-verified at the full 1000-user
  scale (that drain could plausibly take tens of minutes, and the mechanism
  is now proven rather than merely suspected), so this report says exactly
  that rather than implying a scale it didn't actually test.

**What this is, precisely**: a real architectural limitation — no admission
control at the HTTP layer, a worker-thread pool (40) sized well above the
database connection pool (15) it funnels into, and no per-request timeout
budget beyond the pool's own 30s wait — means this deployment absorbs far
more concurrent work than it can promptly process and degrades by queuing
rather than shedding load early. **What it is not**: a deadlock, a resource
leak, or an unbounded hang — every path drains in bounded time. The
concrete, standard fix for the *queuing* behavior specifically (distinct
from the already-named "bigger pool" / "multiple workers" capacity fixes)
is uvicorn's own `--limit-concurrency` flag, which would convert "silently
queue for up to 30s per wave" into "reject the excess immediately with a
fast 503" — a real tradeoff (fail fast vs. eventually succeed) deliberately
left as a named option here rather than silently applied to this project's
deployment config, since choosing between them is a product decision, not
a bug fix.

**Server-reported metrics** (from `GET /stats`, snapshotted before/after
each tier — `lock_contention_count`/`failed_matches` are lifetime,
in-process counters, so figures below are per-tier deltas; `db_p50/95/99`
and `p50/95/99_latency_ms` are end-of-tier snapshots over a bounded
500-sample rolling window):

| Tier | New failed matches | New lock contention events | `match_ride` p99 | DB statement p99 |
|---|---|---|---|---|
| Baseline | 6,996 | 2 | 134.5ms | 18.1ms |
| Tier 2 | 326 | 15 | 61.2ms | 31.5ms |
| Tier 3 | 80 | 14 | 69.1ms | 18.8ms |
| Tier 4 | not captured — server was unresponsive before this snapshot could be taken (see above) | | | |

Lock contention rises with concurrency (2 → 15 → 14 events) even though the
*driver pool* is identical every tier — direct evidence of more simultaneous
`match_ride` calls genuinely racing for the same small set of drivers, not
an artifact of the pool shrinking. `match_ride`'s own p99 latency (tens of
milliseconds) stays far below the request-level p99 seen in the client
table above (which hits 30 seconds) — confirming the bottleneck is
connection-*pool queueing* before `match_ride` even starts running, not the
matching algorithm itself being slow. This is exactly the kind of
distinction Slice 9's separate DB-latency and match-latency instrumentation
was built to make possible.

(Small discrepancies between this table's totals and the client-side table
above — e.g. baseline's 6,996 new failed matches against 6,986 total
`/rides` requests Locust recorded — come from the two being measured over
slightly different windows: Locust's own count closes exactly at its
run-time limit, while the server `/stats` "after" snapshot is captured a
moment later via a separate `curl` call, catching a few more in-flight
completions. Noted rather than silently rounded away.)

**What this benchmark does not claim**: these are single-machine,
single-run numbers on shared, non-dedicated hardware — not a substitute
for a proper multi-run statistical benchmark, and not representative of
this system deployed with a realistically-sized connection pool or
multiple worker processes (the two concrete, named next steps if this
needed to actually sustain 500+ concurrent users — see
`INTERVIEW_PREP.md`'s Slice 9 section).

## Build plan

- [x] **Slice 1 — Core schema + models**
- [x] **Slice 2 — Geospatial matching**
- [x] **Slice 3 — Concurrency safety**
- [x] **Slice 4 — Idempotency**
- [x] **Slice 5 — Surge pricing**
- [x] **Slice 6 — FastAPI layer**
- [x] **Slice 7 — Simulation engine**
- [x] **Slice 8 — Dashboard**
- [x] **Slice 9 — Load testing + chaos testing**
- [ ] Slice 10 — Documentation and polish
