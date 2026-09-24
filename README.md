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

## Build plan

- [x] **Slice 1 — Core schema + models**
- [x] **Slice 2 — Geospatial matching**
- [x] **Slice 3 — Concurrency safety**
- [x] **Slice 4 — Idempotency**
- [ ] Slice 5 — Surge pricing
- [ ] Slice 6 — FastAPI layer
- [ ] Slice 7 — Simulation engine
- [ ] Slice 8 — Dashboard
- [ ] Slice 9 — Load testing + chaos testing
- [ ] Slice 10 — Documentation and polish
