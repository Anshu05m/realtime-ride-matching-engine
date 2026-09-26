"""Slice 7 (CLAUDE.md section 12): proves the simulation's own contention
mechanism actually produces real lock contention against a live server, not
just an in-process approximation.

Requires the FULL stack running for real:
    docker compose up
so an actual uvicorn process is listening on localhost:8000 -- a different
precondition from every other test in this repo, which only need
postgres/redis directly. Skipped automatically if nothing answers on that
port, so the rest of the suite (including CI without the app container
running) isn't blocked by it.

This test creates real rows in whatever database the live server is pointed
at (the same side effect a manual `python -m app.simulation` run already has)
-- it does not clean up after itself, by design, consistent with how the
live stack is otherwise used for simulation runs in this project.

Deliberately uses an area center FAR from the SF coordinate
(37.7749, -122.4194) that every other test/example in this codebase uses by
convention, and that SimulationConfig itself defaults to. That convention is
safe everywhere else because those tests run against the isolated
`ride_matching_test` database -- but this test hits the persistent
`ride_matching` dev database the live server actually uses, which
accumulates real rows from manual `curl`/simulation runs over time. The first
version of this test used the SF default and non-deterministically picked up
an unrelated leftover driver sitting at that exact point from earlier manual
testing (see BUGS_AND_ISSUES.md, Slice 7) -- an extra, real driver made the
match count come out higher than `min(riders, drivers)`, not a matching bug.
"""

import httpx
import pytest

from app.simulation.client import SimulationClient
from app.simulation.config import SimulationConfig
from app.simulation.runner import Simulation

API_URL = "http://localhost:8000"


def _live_server_available() -> bool:
    try:
        httpx.get(f"{API_URL}/health", timeout=1.0)
        return True
    except httpx.HTTPError:
        return False


pytestmark = pytest.mark.skipif(
    not _live_server_available(),
    reason="requires a live server: `docker compose up`",
)


def test_a_barrier_synchronized_burst_never_double_books_a_driver():
    num_drivers = 3
    num_riders = 15

    with httpx.Client(base_url=API_URL, timeout=10.0) as http:
        sim_client = SimulationClient(http)
        config = SimulationConfig(
            api_url=API_URL,
            num_drivers=num_drivers,
            num_riders=num_riders,
            burst_size=num_riders,
            # Far from the SF default -- see module docstring for why.
            area_center_lat=15.0,
            area_center_lng=15.0,
            # Tiny, coincident area+hotspot: guarantees every driver and every
            # rider land in the same/adjacent H3 cells at the default matching
            # resolution, so the candidate search reliably finds all 3 drivers
            # as candidates for every request -- maximizing the odds of real
            # lock contention rather than leaving it to chance.
            area_radius_km=0.1,
            hotspot_radius_km=0.1,
            hotspot_fraction=1.0,
            seed=1234,
        )
        simulation = Simulation(sim_client, config)
        simulation.create_drivers()
        simulation.fire_burst(num_riders)

    outcomes = simulation.results.snapshot()
    assert len(outcomes) == num_riders

    matched = [o for o in outcomes if o.outcome == "matched"]
    assert len(matched) == min(num_riders, num_drivers)

    # The actual invariant under test: no driver was assigned to more than
    # one ride, even though 15 requests raced for only 3 drivers at once.
    driver_ids = [o.driver_id for o in matched]
    assert len(driver_ids) == len(set(driver_ids))

    unmatched = [o for o in outcomes if o.outcome == "unmatched"]
    assert len(unmatched) == num_riders - min(num_riders, num_drivers)

    errors = [o for o in outcomes if o.outcome == "error"]
    assert errors == []
