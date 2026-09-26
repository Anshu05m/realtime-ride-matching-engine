"""Slice 8: end-to-end proof that the sync-to-async event bridge actually
works -- not just that it compiles. Uses the existing `client` TestClient
fixture, which runs real ASGI lifespan (so event_bus.attach() and the
broadcast loop are genuinely running), connects a real WebSocket test
session, then fires ride requests over the same client's ordinary REST
methods and asserts the expected event sequence arrives.

Requires `docker compose up -d postgres redis` running first.
"""

from app.observability.events import event_bus

LAT, LNG = 37.7749, -122.4194


def test_websocket_receives_the_expected_sequence_for_a_successful_match(client):
    client.post("/drivers", json={"current_lat": LAT, "current_lng": LNG})
    rider_id = client.post("/riders", json={"pickup_lat": LAT, "pickup_lng": LNG}).json()["id"]

    with client.websocket_connect("/ws") as ws:
        ride = client.post(
            "/rides", json={"rider_id": rider_id, "pickup_lat": LAT, "pickup_lng": LNG}
        ).json()
        assert ride["status"] == "matched"

        received = [ws.receive_json() for _ in range(4)]

    types = [event["type"] for event in received]
    assert types == ["RIDE_REQUESTED", "CANDIDATES_FOUND", "LOCK_ACQUIRED", "DRIVER_ASSIGNED"]
    assert received[0]["data"]["ride_id"] == ride["id"]
    assert received[3]["data"]["driver_id"] == ride["driver_id"]


def test_websocket_receives_no_driver_available_when_no_driver_exists(client):
    rider_id = client.post("/riders", json={"pickup_lat": 2.0, "pickup_lng": 2.0}).json()["id"]

    with client.websocket_connect("/ws") as ws:
        ride = client.post(
            "/rides", json={"rider_id": rider_id, "pickup_lat": 2.0, "pickup_lng": 2.0}
        ).json()
        assert ride["status"] == "requested"
        assert ride["driver_id"] is None

        received = [ws.receive_json() for _ in range(3)]

    types = [event["type"] for event in received]
    assert types == ["RIDE_REQUESTED", "CANDIDATES_FOUND", "NO_DRIVER_AVAILABLE"]
    assert received[1]["data"]["candidate_count"] == 0


def test_websocket_receives_ride_cancelled(client):
    driver = client.post("/drivers", json={"current_lat": LAT, "current_lng": LNG}).json()
    rider_id = client.post("/riders", json={"pickup_lat": LAT, "pickup_lng": LNG}).json()["id"]
    ride = client.post(
        "/rides", json={"rider_id": rider_id, "pickup_lat": LAT, "pickup_lng": LNG}
    ).json()
    assert ride["status"] == "matched"

    with client.websocket_connect("/ws") as ws:
        client.post(f"/rides/{ride['id']}/cancel")
        event = ws.receive_json()

    assert event["type"] == "RIDE_CANCELLED"
    assert event["data"]["ride_id"] == ride["id"]
    assert event["data"]["driver_id"] == driver["id"]


def test_disconnecting_removes_the_connection_from_the_event_bus_registry(client):
    with client.websocket_connect("/ws"):
        assert len(event_bus._connections) == 1

    assert len(event_bus._connections) == 0
