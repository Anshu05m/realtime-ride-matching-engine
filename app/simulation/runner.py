"""Slice 7: orchestrates a full simulation run against a live API.

Three concurrent loops, each on its own thread:
  - movement: periodically nudges every driver's position and PATCHes it
  - lifecycle: completes/cancels matched rides after a random delay, freeing
    drivers back to AVAILABLE instead of letting the pool monotonically
    deplete as requests get matched
  - request-firing: the main loop, bursts fire_burst() at the configured rate

fire_burst() is exposed as its own method (not buried inside the main loop)
specifically so tests/concurrency/test_simulation_contention.py can call the
real code path instead of reimplementing barrier-synchronized firing.
"""

from __future__ import annotations

import logging
import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from app.simulation.client import SimulationClient
from app.simulation.config import SimulationConfig
from app.simulation.generators import Point, generate_points, random_point_biased
from app.simulation.movement import step as movement_step
from app.simulation.reporting import RequestOutcome, ResultsCollector

logger = logging.getLogger(__name__)


@dataclass
class _TrackedDriver:
    id: str
    position: Point


@dataclass
class _PendingLifecycle:
    ride_id: str
    due_at: float  # time.monotonic() timestamp
    cancel: bool  # True -> cancel at due_at, False -> complete


class Simulation:
    def __init__(self, client: SimulationClient, config: SimulationConfig, rng: random.Random | None = None):
        self.client = client
        self.config = config
        self.rng = rng if rng is not None else random.Random(config.seed)

        self.results = ResultsCollector()
        self._drivers: list[_TrackedDriver] = []
        self._drivers_lock = threading.Lock()

        self._pending_rides: list[_PendingLifecycle] = []
        self._pending_lock = threading.Lock()

        self._stop_event = threading.Event()
        self._riders_fired = 0

    # ---- setup ----

    def create_drivers(self) -> None:
        """Creates config.num_drivers, spread UNIFORMLY across the whole
        area -- deliberately not hotspot-biased. That asymmetry (concentrated
        demand from riders, scattered supply of drivers) is what actually
        produces scarcity for the small set of drivers near the hotspot at
        any given moment, rather than a hardcoded "make it contentious" flag.
        """
        points = generate_points(
            self.config.area_center(), self.config.area_radius_km, self.config.num_drivers, self.rng
        )
        for point in points:
            body = self.client.create_driver(point.lat, point.lng)
            with self._drivers_lock:
                self._drivers.append(_TrackedDriver(id=body["id"], position=point))
        logger.info("created %d drivers", len(self._drivers))

    # ---- movement loop ----

    def _movement_tick(self) -> None:
        with self._drivers_lock:
            drivers_snapshot = list(self._drivers)
        if not drivers_snapshot:
            return

        def move_one(tracked: _TrackedDriver) -> None:
            new_position = movement_step(
                tracked.position,
                area_center=self.config.area_center(),
                area_radius_km=self.config.area_radius_km,
                speed_kmh=self.config.driver_speed_kmh,
                interval_seconds=self.config.movement_interval_seconds,
                rng=self.rng,
            )
            try:
                self.client.update_driver_location(tracked.id, new_position.lat, new_position.lng)
                tracked.position = new_position
            except Exception:
                logger.warning("driver %s movement update failed", tracked.id, exc_info=True)

        with ThreadPoolExecutor(max_workers=min(len(drivers_snapshot), 20)) as executor:
            list(executor.map(move_one, drivers_snapshot))

    def _movement_loop(self) -> None:
        while not self._stop_event.is_set():
            self._movement_tick()
            self._stop_event.wait(self.config.movement_interval_seconds)

    # ---- lifecycle loop ----

    def _schedule_lifecycle(self, ride_id: str) -> None:
        delay = self.rng.uniform(self.config.ride_duration_min_seconds, self.config.ride_duration_max_seconds)
        cancel = self.rng.random() < self.config.cancel_fraction
        with self._pending_lock:
            self._pending_rides.append(
                _PendingLifecycle(ride_id=ride_id, due_at=time.monotonic() + delay, cancel=cancel)
            )

    def _lifecycle_tick(self) -> None:
        now = time.monotonic()
        with self._pending_lock:
            due = [r for r in self._pending_rides if r.due_at <= now]
            self._pending_rides = [r for r in self._pending_rides if r.due_at > now]

        for pending in due:
            try:
                if pending.cancel:
                    self.client.cancel_ride(pending.ride_id)
                else:
                    self.client.complete_ride(pending.ride_id)
            except Exception:
                logger.warning("ride %s lifecycle transition failed", pending.ride_id, exc_info=True)

    def _lifecycle_loop(self) -> None:
        while not self._stop_event.is_set():
            self._lifecycle_tick()
            self._stop_event.wait(1.0)

    # ---- request-firing ----

    def fire_burst(self, size: int) -> None:
        """Fires `size` POST /rides requests synchronized on a
        threading.Barrier so they land on the server together, instead of a
        plain ThreadPoolExecutor.submit() loop -- which lets ordinary
        scheduling/network jitter spread requests out enough that lock
        contention becomes rare and flaky. Mirrors the identical pattern
        tests/concurrency/test_driver_assignment.py already uses.
        """
        riders: list[tuple[str, Point]] = []
        for _ in range(size):
            point = random_point_biased(
                area_center=self.config.area_center(),
                area_radius_km=self.config.area_radius_km,
                hotspot_center=self.config.resolved_hotspot_center(),
                hotspot_radius_km=self.config.resolved_hotspot_radius_km(),
                hotspot_fraction=self.config.hotspot_fraction,
                rng=self.rng,
            )
            try:
                rider_body = self.client.create_rider(point.lat, point.lng)
            except Exception:
                logger.warning("rider creation failed, skipping this slot", exc_info=True)
                continue
            riders.append((rider_body["id"], point))

        if not riders:
            return

        barrier = threading.Barrier(len(riders))

        def worker(rider_id: str, point: Point) -> None:
            barrier.wait()
            start = time.perf_counter()
            try:
                status_code, body = self.client.request_ride(rider_id, point.lat, point.lng)
            except Exception:
                latency = time.perf_counter() - start
                self.results.record(RequestOutcome(outcome="error", latency_seconds=latency))
                logger.warning("ride request failed", exc_info=True)
                return

            latency = time.perf_counter() - start
            if status_code != 201:
                self.results.record(
                    RequestOutcome(outcome="error", latency_seconds=latency, status_code=status_code)
                )
                return

            if body.get("driver_id"):
                self.results.record(
                    RequestOutcome(
                        outcome="matched",
                        latency_seconds=latency,
                        status_code=status_code,
                        ride_id=body["id"],
                        driver_id=body["driver_id"],
                    )
                )
                self._schedule_lifecycle(body["id"])
            else:
                self.results.record(
                    RequestOutcome(
                        outcome="unmatched",
                        latency_seconds=latency,
                        status_code=status_code,
                        ride_id=body.get("id"),
                    )
                )

        with ThreadPoolExecutor(max_workers=len(riders)) as executor:
            list(executor.map(lambda r: worker(*r), riders))

    def _request_loop(self) -> None:
        end_time = time.monotonic() + self.config.duration_seconds
        while (
            not self._stop_event.is_set()
            and time.monotonic() < end_time
            and self._riders_fired < self.config.num_riders
        ):
            remaining = self.config.num_riders - self._riders_fired
            burst = min(self.config.burst_size, remaining)
            if burst <= 0:
                break
            tick_start = time.monotonic()
            self.fire_burst(burst)
            self._riders_fired += burst
            elapsed = time.monotonic() - tick_start
            sleep_for = max(0.0, self.config.tick_interval_seconds - elapsed)
            self._stop_event.wait(sleep_for)

    # ---- orchestration ----

    def run(self) -> list[RequestOutcome]:
        self.create_drivers()

        movement_thread = threading.Thread(target=self._movement_loop, daemon=True)
        lifecycle_thread = threading.Thread(target=self._lifecycle_loop, daemon=True)
        movement_thread.start()
        lifecycle_thread.start()

        try:
            self._request_loop()
        finally:
            self._stop_event.set()
            movement_thread.join(timeout=5)
            lifecycle_thread.join(timeout=5)

        return self.results.snapshot()
