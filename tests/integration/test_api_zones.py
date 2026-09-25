"""API-level test for the zone surge endpoint -- a thin wrapper around
Slice 5's zone_surge(), so this just proves the wiring, not the formula
itself (see tests/unit/test_surge.py for that).

Requires `docker compose up -d postgres redis` running first.
"""


def test_get_zone_surge_for_an_empty_zone_returns_floor_multiplier(client):
    response = client.get("/zones/some-zone-with-no-activity/surge")

    assert response.status_code == 200
    body = response.json()
    assert body["zone_id"] == "some-zone-with-no-activity"
    assert body["demand"] == 0
    assert body["supply"] == 0
    assert body["multiplier"] == 1.0
