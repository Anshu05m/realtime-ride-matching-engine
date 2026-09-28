# Live demo runbook

A short script for recording or presenting the live dashboard (about 2-3 minutes). Every command below was
run against a clean stack while writing this; the numbers you see will differ from run to run.

## Before you start

You need Docker, and a Python 3.12 virtualenv for the simulator:

```bash
python3.12 -m venv venv && ./venv/bin/pip install -e ".[dev]"
```

**Start from an empty database** so leftover drivers from earlier runs don't distort the map or the surge numbers.
This deletes the local dev database (drivers, riders, rides) and the Postgres volume:

```bash
docker compose down -v
docker compose up            # leave running in terminal 1; wait for "Uvicorn running"
```

Check it is healthy: `curl -s localhost:8000/health` should print `{"status":"ok","database":true,"redis":true}`.

Open <http://localhost:8000/dashboard/> in a browser. The connection badge in the header should change from "connecting" to connected;
the map and stats are empty at first because there are no drivers yet.

## The demo

**1. Run the simulation** (terminal 2, from the repo root). The demo area is Pune
(18.5204, 73.8567) — pass it explicitly with `--area-center-lat`/`--area-center-lng`
(the simulation's own default remains the project's original San Francisco coordinates,
unchanged, so existing invocations without these flags are unaffected):

```bash
./venv/bin/python -m app.simulation --area-center-lat 18.5204 --area-center-lng 73.8567 --num-drivers 10 --hotspot-fraction 0.9 --request-rate 0.4 --burst-size 3 --duration-seconds 45
```

This is a deliberately **slower, lower-volume** workload than the tool's own defaults, tuned only
via its existing CLI flags (no code change, no delay added anywhere) so the activity log reads as
a handful of short, followable "chapters" instead of a continuous stream: a barrier-synchronized
burst of 3 requests fires roughly every 7.5 seconds (`burst_size / request_rate`), against a
smaller, more contested pool of 10 drivers (default 50) with 90% of demand concentrated in one
hotspot (default 70%). Verified directly against a live run: each burst produces a clean, readable
sequence — "Ride #4 requested → Ride #4 — Driver matched → Ride #4 completed", and, when two of
the burst's requests land on the same nearest driver, "Ride #1 requested → Ride #1 — Driver busy
— retrying next candidate → Ride #1 — Driver matched → Ride #1 completed" — with real lock
contention and surge above 1.0x (up to 2.0x in one verification run) still appearing naturally,
just at a pace that leaves several quiet seconds between bursts to talk over what just happened.
Every ride gets its own stable "Ride #N" tag the first time it's mentioned (assigned client-side
in the browser, in the order each ride is first seen — never the ride's real id), so you can point
at one ride's number and trace its full story as later lines for the same number scroll in,
even while other rides' lines are interleaved with it. For a repeat of the exact run this was
verified against, add `--seed 7`. All options: `./venv/bin/python -m app.simulation --help`.
The dashboard's own initial map view (before any driver exists) is centered on the same Pune
coordinates; it re-fits to the actual drivers as soon as the first poll returns any, regardless of area.

**2. What to point at while it runs** (the stats and map refresh about every 3 seconds; the event log is live):

| On screen | What it shows |
|---|---|
| Map markers | Drivers by status (available / busy), moving as the simulation moves them |
| Activity log: "Ride #N requested" → "Ride #N — Driver matched" | One request's path through matching, traceable by its own number |
| Activity log: "Ride #N — Driver busy — retrying next candidate" | Two requests raced for the same driver; the loser moves on to the next candidate |
| Activity log: "Ride #N — No driver available" | A request found no free driver; the ride stays `requested` and the API still returned 201 |
| Top stat tiles: Active rides / Completed rides / No driver available / Throughput / Match latency p95 | The 5 glanceable numbers; live from `GET /stats` |
| "More metrics" (collapsed) | Available/busy drivers, cancelled rides, p50/p99 latency — same data as before, just tucked away |
| Surge summary: active zones, peak now, highest this session | Zone breakdown (collapsed) gives the raw demand/supply/multiplier per zone if asked |

**3. Talking points** (see the README for detail):

- Redis lock reduces contention; the Postgres partial unique index is what actually prevents a driver having two
  active rides. "Driver busy — retrying next candidate" lines are the lock doing its job.
- Idempotency: in another terminal, repeat a `POST /rides` with the same `Idempotency-Key` (examples in the README's
  API section) and show the same ride id comes back.

**4. Cross-check** when the simulation ends: its printed summary ("Server-side GET /stats") should agree with the
dashboard's numbers, since both read the same `/stats`.

## Between takes

Reset to a clean slate exactly as in "Before you start" (`docker compose down -v`, then `docker compose up`).
Restarting only the app clears the in-memory counters and latency window but keeps the database contents.

## Not covered here

Load and failure testing are not part of the live demo: they are documented in
[docs/benchmarks.md](../docs/benchmarks.md) and [docs/chaos-testing.md](../docs/chaos-testing.md).
