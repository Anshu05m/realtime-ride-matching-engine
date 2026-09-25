"""API-level test for GET /stats.

Requires `docker compose up -d postgres redis` running first.
"""

LAT, LNG = 37.7749, -122.4194


def test_stats_reflects_driver_and_ride_counts(client):
    baseline = client.get("/stats").json()

    client.post("/drivers", json={"current_lat": LAT, "current_lng": LNG})
    driver_resp = client.post("/drivers", json={"current_lat": LAT, "current_lng": LNG}).json()
    rider_id = client.post("/riders", json={"pickup_lat": LAT, "pickup_lng": LNG}).json()["id"]
    ride = client.post(
        "/rides", json={"rider_id": rider_id, "pickup_lat": LAT, "pickup_lng": LNG}
    ).json()
    assert ride["status"] == "matched"
    client.post(f"/rides/{ride['id']}/complete")

    response = client.get("/stats")

    assert response.status_code == 200
    body = response.json()
    assert body["available_drivers"] == baseline["available_drivers"] + 2
    assert body["completed_rides"] == baseline["completed_rides"] + 1
    assert "latency" not in body and "throughput" not in body
    assert set(body.keys()) == {
        "available_drivers",
        "busy_drivers",
        "active_rides",
        "completed_rides",
        "cancelled_rides",
    }
