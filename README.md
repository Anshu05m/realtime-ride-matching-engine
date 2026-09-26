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

## Build plan

- [x] **Slice 1 — Core schema + models**
- [x] **Slice 2 — Geospatial matching**
- [x] **Slice 3 — Concurrency safety**
- [x] **Slice 4 — Idempotency**
- [x] **Slice 5 — Surge pricing**
- [x] **Slice 6 — FastAPI layer**
- [x] **Slice 7 — Simulation engine**
- [ ] Slice 8 — Dashboard
- [ ] Slice 9 — Load testing + chaos testing
- [ ] Slice 10 — Documentation and polish
