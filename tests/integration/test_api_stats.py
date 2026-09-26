"""API-level test for GET /stats.

Requires `docker compose up -d postgres redis` running first.

Slice 8 note: failed_matches/match_throughput_per_minute/p50-99_latency_ms
come from `app.observability.metrics.metrics_tracker`, a single process-wide
singleton that is NOT reset between tests (unlike the DB-backed fields,
which get real per-test isolation from the `db_session` fixture's
SAVEPOINT rollback). That's a deliberate, documented consequence of this
being genuine shared process state, modeling how a real long-running
server's in-memory metrics actually behave -- so these tests assert
relative deltas and shape/type, never exact absolute values.
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
    assert set(body.keys()) == {
        "available_drivers",
        "busy_drivers",
        "active_rides",
        "completed_rides",
        "cancelled_rides",
        "failed_matches",
        "match_throughput_per_minute",
        "p50_latency_ms",
        "p95_latency_ms",
        "p99_latency_ms",
        "surge_by_zone",
    }
    assert driver_resp["status"] == "available"


def test_stats_latency_fields_are_non_negative_after_a_match(client):
    client.post("/drivers", json={"current_lat": LAT, "current_lng": LNG})
    rider_id = client.post("/riders", json={"pickup_lat": LAT, "pickup_lng": LNG}).json()["id"]
    ride = client.post(
        "/rides", json={"rider_id": rider_id, "pickup_lat": LAT, "pickup_lng": LNG}
    ).json()
    assert ride["status"] == "matched"

    body = client.get("/stats").json()

    assert body["p50_latency_ms"] >= 0
    assert body["p95_latency_ms"] >= body["p50_latency_ms"]
    assert body["p99_latency_ms"] >= body["p95_latency_ms"]
    assert body["match_throughput_per_minute"] >= 1


def test_stats_failed_matches_increments_when_no_driver_is_available(client):
    baseline = client.get("/stats").json()["failed_matches"]

    # No drivers anywhere near this pickup point -> NoAvailableDriverError.
    rider_id = client.post("/riders", json={"pickup_lat": 1.0, "pickup_lng": 1.0}).json()["id"]
    ride = client.post(
        "/rides", json={"rider_id": rider_id, "pickup_lat": 1.0, "pickup_lng": 1.0}
    ).json()
    assert ride["status"] == "requested"
    assert ride["driver_id"] is None

    body = client.get("/stats").json()
    assert body["failed_matches"] == baseline + 1


def test_stats_surge_by_zone_includes_a_zone_with_a_recent_ride(client):
    driver = client.post("/drivers", json={"current_lat": LAT, "current_lng": LNG}).json()
    rider_id = client.post("/riders", json={"pickup_lat": LAT, "pickup_lng": LNG}).json()["id"]
    client.post("/rides", json={"rider_id": rider_id, "pickup_lat": LAT, "pickup_lng": LNG})

    body = client.get("/stats").json()

    assert driver["zone_id"] in body["surge_by_zone"]
    zone_entry = body["surge_by_zone"][driver["zone_id"]]
    assert zone_entry["zone_id"] == driver["zone_id"]
    assert zone_entry["demand"] >= 1
