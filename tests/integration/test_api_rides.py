"""API-level tests for the ride endpoints: creation (+ the create-then-match
orchestration and idempotency-header passthrough), status, cancel, complete.

Requires `docker compose up -d postgres redis` running first.
"""

import uuid

LAT, LNG = 37.7749, -122.4194


def _create_rider(client) -> str:
    return client.post("/riders", json={"pickup_lat": LAT, "pickup_lng": LNG}).json()["id"]


def _create_driver(client) -> str:
    return client.post("/drivers", json={"current_lat": LAT, "current_lng": LNG}).json()["id"]


def test_request_ride_with_an_available_driver_returns_matched_ride(client):
    rider_id = _create_rider(client)
    driver_id = _create_driver(client)

    response = client.post(
        "/rides", json={"rider_id": rider_id, "pickup_lat": LAT, "pickup_lng": LNG}
    )

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "matched"
    assert body["driver_id"] == driver_id
    assert body["matched_at"] is not None
    assert body["surge_multiplier"] is not None
    assert body["zone_id"] is not None


def test_request_ride_with_no_driver_is_still_a_201(client):
    # No driver created at all -- match_ride raises NoAvailableDriverError,
    # which is NOT a client error: the ride is still successfully created.
    rider_id = _create_rider(client)

    response = client.post(
        "/rides", json={"rider_id": rider_id, "pickup_lat": LAT, "pickup_lng": LNG}
    )

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "requested"
    assert body["driver_id"] is None


def test_request_ride_rejects_out_of_range_coordinates(client):
    rider_id = _create_rider(client)

    response = client.post(
        "/rides", json={"rider_id": rider_id, "pickup_lat": 999.0, "pickup_lng": LNG}
    )

    assert response.status_code == 422


def test_idempotency_key_returns_the_same_ride_on_retry(client):
    rider_id = _create_rider(client)
    key = str(uuid.uuid4())

    first = client.post(
        "/rides",
        json={"rider_id": rider_id, "pickup_lat": LAT, "pickup_lng": LNG},
        headers={"Idempotency-Key": key},
    )
    second = client.post(
        "/rides",
        json={"rider_id": rider_id, "pickup_lat": LAT, "pickup_lng": LNG},
        headers={"Idempotency-Key": key},
    )

    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["id"] == second.json()["id"]


def test_idempotency_key_with_different_params_returns_409(client):
    rider_id = _create_rider(client)
    key = str(uuid.uuid4())

    client.post(
        "/rides",
        json={"rider_id": rider_id, "pickup_lat": LAT, "pickup_lng": LNG},
        headers={"Idempotency-Key": key},
    )
    conflicting = client.post(
        "/rides",
        json={"rider_id": rider_id, "pickup_lat": LAT + 10, "pickup_lng": LNG},
        headers={"Idempotency-Key": key},
    )

    assert conflicting.status_code == 409
    assert conflicting.json()["error"]["code"] == "idempotency_key_conflict"


def test_empty_idempotency_key_header_is_treated_as_no_key(client):
    rider_id = _create_rider(client)

    first = client.post(
        "/rides",
        json={"rider_id": rider_id, "pickup_lat": LAT, "pickup_lng": LNG},
        headers={"Idempotency-Key": ""},
    )
    second = client.post(
        "/rides",
        json={"rider_id": rider_id, "pickup_lat": LAT, "pickup_lng": LNG},
        headers={"Idempotency-Key": ""},
    )

    assert first.json()["id"] != second.json()["id"]


def test_get_ride_returns_the_created_ride(client):
    rider_id = _create_rider(client)
    created = client.post(
        "/rides", json={"rider_id": rider_id, "pickup_lat": LAT, "pickup_lng": LNG}
    ).json()

    response = client.get(f"/rides/{created['id']}")

    assert response.status_code == 200
    assert response.json()["id"] == created["id"]


def test_get_unknown_ride_returns_structured_404(client):
    response = client.get(f"/rides/{uuid.uuid4()}")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "ride_not_found"


def test_cancel_ride_frees_the_driver(client):
    rider_id = _create_rider(client)
    driver_id = _create_driver(client)
    ride = client.post(
        "/rides", json={"rider_id": rider_id, "pickup_lat": LAT, "pickup_lng": LNG}
    ).json()
    assert ride["status"] == "matched"

    response = client.post(f"/rides/{ride['id']}/cancel")

    assert response.status_code == 200
    assert response.json()["status"] == "cancelled"
    assert client.get(f"/drivers/{driver_id}").json()["status"] == "available"


def test_cancel_already_cancelled_ride_returns_409(client):
    rider_id = _create_rider(client)
    ride = client.post(
        "/rides", json={"rider_id": rider_id, "pickup_lat": LAT, "pickup_lng": LNG}
    ).json()
    client.post(f"/rides/{ride['id']}/cancel")

    response = client.post(f"/rides/{ride['id']}/cancel")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "invalid_ride_state"


def test_cancel_unknown_ride_returns_404(client):
    response = client.post(f"/rides/{uuid.uuid4()}/cancel")

    assert response.status_code == 404


def test_complete_matched_ride_frees_the_driver(client):
    rider_id = _create_rider(client)
    driver_id = _create_driver(client)
    ride = client.post(
        "/rides", json={"rider_id": rider_id, "pickup_lat": LAT, "pickup_lng": LNG}
    ).json()
    assert ride["status"] == "matched"

    response = client.post(f"/rides/{ride['id']}/complete")

    assert response.status_code == 200
    assert response.json()["status"] == "completed"
    assert client.get(f"/drivers/{driver_id}").json()["status"] == "available"


def test_complete_a_driverless_requested_ride_returns_409(client):
    rider_id = _create_rider(client)
    ride = client.post(
        "/rides", json={"rider_id": rider_id, "pickup_lat": LAT, "pickup_lng": LNG}
    ).json()
    assert ride["status"] == "requested"

    response = client.post(f"/rides/{ride['id']}/complete")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "invalid_ride_state"


def test_complete_unknown_ride_returns_404(client):
    response = client.post(f"/rides/{uuid.uuid4()}/complete")

    assert response.status_code == 404
