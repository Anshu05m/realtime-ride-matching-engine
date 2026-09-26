"""Slice 9: Locust load test -- CLAUDE.md's own separate tool from Slice 7's
simulation engine (a deliberately different traffic generator: Locust's own
idioms and stats, not a reimplementation of app/simulation/'s barrier-
synchronized bursts, which exist for the dashboard demo, not benchmarking).

Driver pool is DELIBERATELY CONSTRAINED (DRIVER_POOL_SIZE below), not sized
to avoid exhaustion. A first draft sized it generously so supply never ran
out; that would have produced a uniformly-green, less credible result at
every concurrency tier and hidden exactly the data CLAUDE.md's benchmark
checklist asks for (successful/failed match %, lock contention are most
interesting under real scarcity). This is an owned, stated choice, not an
accident: ~matches the lowest tested concurrency tier, deliberately too
small for the 500/1000-user tiers, so match-rate degradation under load is
real and visible in the results, not engineered away.

test_start resets (TRUNCATEs) and reseeds a fresh pool before EVERY separate
headless `locust` invocation, so each of run_benchmarks.sh's tiers starts
from an identical clean baseline rather than inheriting whatever the
previous tier's run didn't consume.

Run via load_tests/run_benchmarks.sh, not directly -- that script also
starts the benchmark target without uvicorn's --reload (docker-compose.yml's
dev command uses --reload, which adds file-watching overhead not
representative of the Dockerfile's plain production CMD).
"""

import random

import httpx
from locust import HttpUser, between, events, task
from sqlalchemy import create_engine, text

from app.config import settings
from app.simulation.generators import Point, random_point_in_radius

AREA_CENTER = Point(lat=37.7749, lng=-122.4194)  # matches this project's own convention
AREA_RADIUS_KM = 2.0
DRIVER_POOL_SIZE = 50


@events.test_start.add_listener
def reset_and_seed_drivers(environment, **kwargs):
    """Runs once at the start of every separate headless `locust`
    invocation -- see module docstring for why a full reset (not additive
    seeding) matters for comparing tiers fairly."""
    engine = create_engine(settings.database_url)
    with engine.begin() as conn:
        conn.execute(text("TRUNCATE TABLE rides, drivers, riders RESTART IDENTITY CASCADE"))
    engine.dispose()

    rng = random.Random()
    with httpx.Client(base_url=environment.host, timeout=10.0) as http:
        for _ in range(DRIVER_POOL_SIZE):
            point = random_point_in_radius(AREA_CENTER, AREA_RADIUS_KM, rng)
            http.post("/drivers", json={"current_lat": point.lat, "current_lng": point.lng})


class RideRequestUser(HttpUser):
    # Near-zero wait: this is a load test measuring throughput/latency under
    # real load, not a realistic-think-time traffic shape (that's Slice 7's
    # simulation engine's job).
    wait_time = between(0, 0.1)

    def on_start(self) -> None:
        self._rng = random.Random()

    @task
    def request_ride(self) -> None:
        point = random_point_in_radius(AREA_CENTER, AREA_RADIUS_KM, self._rng)
        rider_response = self.client.post(
            "/riders",
            json={"pickup_lat": point.lat, "pickup_lng": point.lng},
            name="/riders",
        )
        if rider_response.status_code != 201:
            return
        rider_id = rider_response.json()["id"]
        self.client.post(
            "/rides",
            json={"rider_id": rider_id, "pickup_lat": point.lat, "pickup_lng": point.lng},
            name="/rides",
        )
