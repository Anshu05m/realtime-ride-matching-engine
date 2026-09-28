# Load Testing / Benchmarks

> Part of the [Real-Time Ride-Matching Engine](../README.md). Full methodology,
> results, and the saturation investigation behind the README's benchmark
> summary. Raw evidence (CSV/JSON from every run) is committed under
> [`load_tests/results/`](../load_tests/results/). References below to
> `INTERVIEW_PREP.md` and `docs/plans/` are maintainer-side notes that are not
> part of the published repository.

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
Slice 9's own `database_error` fix (see [chaos-testing.md](chaos-testing.md))
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
  crash — this is Slice 9's own fix from [chaos-testing.md](chaos-testing.md) firing
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
