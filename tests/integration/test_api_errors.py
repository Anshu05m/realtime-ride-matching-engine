"""Direct tests of the structured error envelope itself --
{"error": {"code": "...", "message": "..."}} -- for every exception type
app/main.py registers a handler for. Cross-cutting infrastructure like this
is exactly where "works for route A but not route B" bugs hide, so it gets
its own coverage rather than relying on each endpoint's individual
error-path test to incidentally prove the envelope is uniform everywhere.

Requires `docker compose up -d postgres redis` running first.
"""

import uuid

LAT, LNG = 37.7749, -122.4194


def _assert_envelope(response, status_code: int, code: str):
    assert response.status_code == status_code
    body = response.json()
    assert set(body.keys()) == {"error"}
    assert set(body["error"].keys()) == {"code", "message"}
    assert body["error"]["code"] == code
    assert isinstance(body["error"]["message"], str) and body["error"]["message"]


def test_pydantic_validation_error_envelope(client):
    response = client.post("/drivers", json={"current_lat": 999.0, "current_lng": LNG})
    _assert_envelope(response, 422, "validation_error")


def test_plain_http_exception_404_envelope(client):
    response = client.get(f"/drivers/{uuid.uuid4()}")
    _assert_envelope(response, 404, "driver_not_found")


def test_ride_not_found_domain_exception_envelope(client):
    # Goes through ride_service.cancel_ride, which raises the domain
    # RideNotFoundError -- a different code path than the plain
    # HTTPException GET /drivers/{id} uses above, but must produce the
    # identical envelope shape.
    response = client.post(f"/rides/{uuid.uuid4()}/cancel")
    _assert_envelope(response, 404, "ride_not_found")


def test_idempotency_key_conflict_envelope(client):
    rider_id = client.post("/riders", json={"pickup_lat": LAT, "pickup_lng": LNG}).json()["id"]
    key = str(uuid.uuid4())
    client.post(
        "/rides",
        json={"rider_id": rider_id, "pickup_lat": LAT, "pickup_lng": LNG},
        headers={"Idempotency-Key": key},
    )

    response = client.post(
        "/rides",
        json={"rider_id": rider_id, "pickup_lat": LAT + 10, "pickup_lng": LNG},
        headers={"Idempotency-Key": key},
    )

    _assert_envelope(response, 409, "idempotency_key_conflict")


def test_invalid_ride_state_envelope(client):
    rider_id = client.post("/riders", json={"pickup_lat": LAT, "pickup_lng": LNG}).json()["id"]
    ride = client.post(
        "/rides", json={"rider_id": rider_id, "pickup_lat": LAT, "pickup_lng": LNG}
    ).json()
    client.post(f"/rides/{ride['id']}/cancel")

    response = client.post(f"/rides/{ride['id']}/cancel")

    _assert_envelope(response, 409, "invalid_ride_state")


def test_unmatched_route_still_uses_the_structured_envelope(client):
    # A framework-level 404 (no route at all), not one our own code raises --
    # proves the handler is registered against the Starlette base class, not
    # just fastapi.HTTPException instances our routes happen to raise.
    response = client.get("/this-route-does-not-exist")
    _assert_envelope(response, 404, "http_error")
