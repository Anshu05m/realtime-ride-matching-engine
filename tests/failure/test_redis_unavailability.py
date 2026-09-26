"""Slice 9: proves the fix for a real bug found while writing this slice's
chaos tests -- Redis being unreachable during lock acquisition used to
propagate as an unhandled, unstructured 500. Tests the FIXED behavior (a
structured 503) through the real HTTP layer, plus proves the narrow except
clause (ConnectionError/TimeoutError only) doesn't over-reach into actually
misattributing an application bug (ResponseError) as infrastructure being
down.

Requires `docker compose up -d postgres redis` running first.
"""

import pytest
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import ResponseError
from redis.exceptions import TimeoutError as RedisTimeoutError

from app.redis.client import redis_client

LAT, LNG = 37.7749, -122.4194


def test_redis_connection_error_during_lock_acquisition_returns_a_structured_503(
    client, monkeypatch
):
    client.post("/drivers", json={"current_lat": LAT, "current_lng": LNG})
    rider_id = client.post("/riders", json={"pickup_lat": LAT, "pickup_lng": LNG}).json()["id"]

    def _raise_connection_error(*args, **kwargs):
        raise RedisConnectionError("simulated redis outage")

    monkeypatch.setattr(redis_client, "set", _raise_connection_error)

    response = client.post(
        "/rides", json={"rider_id": rider_id, "pickup_lat": LAT, "pickup_lng": LNG}
    )

    assert response.status_code == 503
    body = response.json()
    assert body["error"]["code"] == "locking_unavailable"
    assert "simulated redis outage" not in body["error"]["message"]


def test_redis_timeout_error_also_returns_a_structured_503(client, monkeypatch):
    """Distinct exception type from the connection-error case -- proves the
    handler is reached via the exception hierarchy match_ride raises
    LockingUnavailableError for (both ConnectionError and TimeoutError), not
    one specific error class coincidentally."""
    client.post("/drivers", json={"current_lat": LAT, "current_lng": LNG})
    rider_id = client.post("/riders", json={"pickup_lat": LAT, "pickup_lng": LNG}).json()["id"]

    def _raise_timeout(*args, **kwargs):
        raise RedisTimeoutError("simulated redis timeout")

    monkeypatch.setattr(redis_client, "set", _raise_timeout)

    response = client.post(
        "/rides", json={"rider_id": rider_id, "pickup_lat": LAT, "pickup_lng": LNG}
    )

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "locking_unavailable"


def test_a_response_error_is_not_misattributed_as_locking_unavailable(client, monkeypatch):
    """A malformed-command-style RedisError (ResponseError) is an actual
    application bug, not infrastructure being down -- must NOT be silently
    reclassified as locking_unavailable, per the deliberately narrow except
    clause in app/matching/matcher.py (ConnectionError/TimeoutError only,
    not the broad RedisError). No handler exists for ResponseError, so it
    genuinely propagates uncaught -- Starlette's TestClient re-raises an
    unhandled server exception in the calling test rather than returning a
    500 response, which is itself the proof nothing quietly intercepts it."""
    client.post("/drivers", json={"current_lat": LAT, "current_lng": LNG})
    rider_id = client.post("/riders", json={"pickup_lat": LAT, "pickup_lng": LNG}).json()["id"]

    def _raise_response_error(*args, **kwargs):
        raise ResponseError("simulated malformed command")

    monkeypatch.setattr(redis_client, "set", _raise_response_error)

    with pytest.raises(ResponseError):
        client.post(
            "/rides", json={"rider_id": rider_id, "pickup_lat": LAT, "pickup_lng": LNG}
        )
