"""Slice 7: one sequential smoke test proving SimulationClient's HTTP methods
round-trip correctly against the real API schemas.

Deliberately NOT a concurrency test -- the shared, SAVEPOINT-isolated `client`
fixture (tests/conftest.py) is not safe for genuine multi-threaded use (the
same reason Slice 3 built a dedicated fixture for real concurrency tests, see
tests/concurrency/conftest.py). Real contention is proven by
tests/concurrency/test_simulation_contention.py against a live server.
"""

from app.simulation.client import SimulationClient


def test_simulation_client_round_trips_against_the_real_api(client):
    sim_client = SimulationClient(client)

    driver = sim_client.create_driver(37.7749, -122.4194)
    assert driver["status"] == "available"

    rider = sim_client.create_rider(37.7749, -122.4194)

    status_code, ride_body = sim_client.request_ride(rider["id"], 37.7749, -122.4194)
    assert status_code == 201
    assert ride_body["driver_id"] == driver["id"]
    assert ride_body["status"] == "matched"

    fetched = sim_client.get_ride(ride_body["id"])
    assert fetched["id"] == ride_body["id"]

    completed = sim_client.complete_ride(ride_body["id"])
    assert completed["status"] == "completed"

    moved = sim_client.update_driver_location(driver["id"], 37.78, -122.42)
    assert moved["current_lat"] == 37.78

    stats = sim_client.get_stats()
    assert "available_drivers" in stats

    surge = sim_client.get_zone_surge(driver["zone_id"])
    assert surge["zone_id"] == driver["zone_id"]


def test_simulation_client_reports_unmatched_ride_when_no_driver_exists(client):
    sim_client = SimulationClient(client)

    rider = sim_client.create_rider(10.0, 10.0)
    status_code, ride_body = sim_client.request_ride(rider["id"], 10.0, 10.0)

    assert status_code == 201
    assert ride_body["driver_id"] is None
    assert ride_body["status"] == "requested"


def test_simulation_client_cancel_ride_frees_the_driver(client):
    sim_client = SimulationClient(client)

    driver = sim_client.create_driver(1.0, 1.0)
    rider = sim_client.create_rider(1.0, 1.0)
    _, ride_body = sim_client.request_ride(rider["id"], 1.0, 1.0)

    cancelled = sim_client.cancel_ride(ride_body["id"])
    assert cancelled["status"] == "cancelled"

    refreshed_driver = sim_client.update_driver_location(driver["id"], 1.0, 1.0)
    assert refreshed_driver["status"] == "available"
