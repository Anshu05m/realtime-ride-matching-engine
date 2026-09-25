"""API-level tests for the driver endpoints.

Requires `docker compose up -d postgres redis` running first.
"""

LAT, LNG = 37.7749, -122.4194


def test_create_driver_returns_201_with_computed_h3_and_zone(client):
    response = client.post("/drivers", json={"current_lat": LAT, "current_lng": LNG})

    assert response.status_code == 201
    body = response.json()
    assert body["current_lat"] == LAT
    assert body["current_lng"] == LNG
    assert body["status"] == "available"
    assert body["h3_index"]
    assert body["zone_id"]


def test_create_driver_rejects_out_of_range_latitude(client):
    response = client.post("/drivers", json={"current_lat": 999.0, "current_lng": LNG})

    assert response.status_code == 422
    body = response.json()
    assert body["error"]["code"] == "validation_error"


def test_get_driver_returns_the_created_driver(client):
    created = client.post("/drivers", json={"current_lat": LAT, "current_lng": LNG}).json()

    response = client.get(f"/drivers/{created['id']}")

    assert response.status_code == 200
    assert response.json()["id"] == created["id"]


def test_get_unknown_driver_returns_structured_404(client):
    import uuid

    response = client.get(f"/drivers/{uuid.uuid4()}")

    assert response.status_code == 404
    body = response.json()
    assert body["error"]["code"] == "driver_not_found"


def test_update_driver_location_recomputes_h3_and_zone(client):
    created = client.post("/drivers", json={"current_lat": LAT, "current_lng": LNG}).json()

    response = client.patch(
        f"/drivers/{created['id']}/location", json={"lat": LAT + 5, "lng": LNG + 5}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["current_lat"] == LAT + 5
    assert body["h3_index"] != created["h3_index"]


def test_update_unknown_driver_location_returns_404(client):
    import uuid

    response = client.patch(
        f"/drivers/{uuid.uuid4()}/location", json={"lat": LAT, "lng": LNG}
    )

    assert response.status_code == 404
