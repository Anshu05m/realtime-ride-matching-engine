"""Slice 7: thin HTTP client for the simulation engine.

Mirrors this codebase's repository-layer convention (app/storage/repositories/*)
translated to an HTTP context: one named method per endpoint actually used, no
generic "call this URL" helper, so call sites read as intent
(`client.request_ride(...)`) rather than inline httpx calls scattered through
runner.py.

Wraps an INJECTED httpx-compatible client rather than owning one -- production
code (__main__.py) passes a real httpx.Client(base_url=...); tests pass the
existing tests/conftest.py `client` TestClient fixture, which subclasses
httpx.Client and satisfies the same interface, so there's no need for a
second, duplicated way to stand up the app for HTTP-level testing.
"""

from __future__ import annotations

import uuid
from typing import Any, Protocol


class HttpClient(Protocol):
    def get(self, url: str, **kwargs: Any) -> Any: ...
    def post(self, url: str, **kwargs: Any) -> Any: ...
    def patch(self, url: str, **kwargs: Any) -> Any: ...


class SimulationClient:
    def __init__(self, http: HttpClient):
        self.http = http

    def create_driver(self, lat: float, lng: float) -> dict[str, Any]:
        resp = self.http.post("/drivers", json={"current_lat": lat, "current_lng": lng})
        resp.raise_for_status()
        return resp.json()

    def update_driver_location(self, driver_id: uuid.UUID | str, lat: float, lng: float) -> dict[str, Any]:
        resp = self.http.patch(f"/drivers/{driver_id}/location", json={"lat": lat, "lng": lng})
        resp.raise_for_status()
        return resp.json()

    def create_rider(self, lat: float, lng: float) -> dict[str, Any]:
        resp = self.http.post("/riders", json={"pickup_lat": lat, "pickup_lng": lng})
        resp.raise_for_status()
        return resp.json()

    def request_ride(self, rider_id: uuid.UUID | str, lat: float, lng: float) -> tuple[int, dict[str, Any]]:
        """Returns (status_code, body) rather than raising on non-2xx: callers
        (runner.py's burst-firing) need to distinguish "matched", "unmatched
        but still 201", and a genuine error -- POST /rides always returns 201
        for the first two cases (see app/api/rides.py), so there's no
        exception-worthy outcome to raise on except a real error, and the
        caller is better placed than this method to decide what "error" means
        for its own bookkeeping.
        """
        resp = self.http.post(
            "/rides",
            json={"rider_id": str(rider_id), "pickup_lat": lat, "pickup_lng": lng},
        )
        content_type = resp.headers.get("content-type", "")
        body = resp.json() if content_type.startswith("application/json") else {}
        return resp.status_code, body

    def get_ride(self, ride_id: uuid.UUID | str) -> dict[str, Any]:
        resp = self.http.get(f"/rides/{ride_id}")
        resp.raise_for_status()
        return resp.json()

    def cancel_ride(self, ride_id: uuid.UUID | str) -> dict[str, Any]:
        resp = self.http.post(f"/rides/{ride_id}/cancel")
        resp.raise_for_status()
        return resp.json()

    def complete_ride(self, ride_id: uuid.UUID | str) -> dict[str, Any]:
        resp = self.http.post(f"/rides/{ride_id}/complete")
        resp.raise_for_status()
        return resp.json()

    def get_stats(self) -> dict[str, Any]:
        resp = self.http.get("/stats")
        resp.raise_for_status()
        return resp.json()

    def get_zone_surge(self, zone_id: str) -> dict[str, Any]:
        resp = self.http.get(f"/zones/{zone_id}/surge")
        resp.raise_for_status()
        return resp.json()
