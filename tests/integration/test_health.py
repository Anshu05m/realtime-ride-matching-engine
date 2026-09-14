"""Requires `docker compose up -d postgres redis` running, and a valid
DATABASE_URL/REDIS_URL (via .env) pointing at them."""

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health_reports_database_and_redis_up():
    response = client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body == {"status": "ok", "database": True, "redis": True}
