"""Slice 9: proves the fix for a real bug found while writing this slice's
chaos tests -- a generic database error during a commit used to propagate
as an unhandled, unstructured 500, contradicting app/main.py's own stated
"every error is a structured envelope" invariant. Tests the FIXED behavior
(a structured 503) through the real HTTP layer, since the fix lives in
app/main.py's exception handler, not in application logic.

Doesn't need to target match_ride's commit specifically (vs. create_ride's):
the handler catches sqlalchemy.exc.SQLAlchemyError regardless of which
commit call in the request raised it, so patching the shared session's
commit unconditionally is equally valid evidence, and far simpler than
trying to make only the Nth commit call fail.

Requires `docker compose up -d postgres redis` running first.
"""

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.exc import TimeoutError as SQLATimeoutError

LAT, LNG = 37.7749, -122.4194


def test_a_database_error_during_commit_returns_a_structured_503(client, db_session, monkeypatch):
    rider_id = client.post("/riders", json={"pickup_lat": LAT, "pickup_lng": LNG}).json()["id"]

    def _raise_db_error():
        raise SQLAlchemyError("simulated database failure -- must never reach the client")

    monkeypatch.setattr(db_session, "commit", _raise_db_error)

    response = client.post(
        "/rides", json={"rider_id": rider_id, "pickup_lat": LAT, "pickup_lng": LNG}
    )

    assert response.status_code == 503
    body = response.json()
    assert body["error"]["code"] == "database_error"
    # A raw driver/db error message must never leak into the client-facing
    # response -- only a fixed, generic message; the real exception is
    # logged server-side instead (see app/main.py's database_error_handler).
    assert "simulated database failure" not in body["error"]["message"]


def test_a_pool_timeout_error_also_returns_a_structured_503(client, db_session, monkeypatch):
    """Distinct exception subtype from the OperationalError-shaped case
    above -- proves the handler catches the BASE SQLAlchemyError class, not
    one specific subtype it happens to have been written against.
    sqlalchemy.exc.TimeoutError (connection pool exhaustion, default
    pool_size=5 + max_overflow=10) is a real failure mode load_tests/ is
    likely to actually trigger at high concurrency, not just a hypothetical."""
    rider_id = client.post("/riders", json={"pickup_lat": LAT, "pickup_lng": LNG}).json()["id"]

    def _raise_pool_timeout():
        raise SQLATimeoutError("simulated connection pool exhaustion")

    monkeypatch.setattr(db_session, "commit", _raise_pool_timeout)

    response = client.post(
        "/rides", json={"rider_id": rider_id, "pickup_lat": LAT, "pickup_lng": LNG}
    )

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "database_error"
