"""API-level tests for the rider endpoint.

Requires `docker compose up -d postgres redis` running first.
"""

LAT, LNG = 37.7749, -122.4194


def test_create_rider_returns_201(client):
    response = client.post("/riders", json={"pickup_lat": LAT, "pickup_lng": LNG})

    assert response.status_code == 201
    body = response.json()
    assert body["pickup_lat"] == LAT
    assert body["pickup_lng"] == LNG
    assert "id" in body


def test_create_rider_rejects_out_of_range_longitude(client):
    response = client.post("/riders", json={"pickup_lat": LAT, "pickup_lng": 999.0})

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
