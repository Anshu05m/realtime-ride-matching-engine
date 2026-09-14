from fastapi import FastAPI
from sqlalchemy import text

from app.redis.client import redis_client
from app.storage.database import SessionLocal

app = FastAPI(title="Real-Time Ride-Matching Engine")


@app.get("/health")
def health() -> dict:
    """Confirms the two durable dependencies (Postgres, Redis) are reachable.
    Domain endpoints (drivers/riders/rides) arrive in Slice 6."""
    db_ok = False
    try:
        db = SessionLocal()
        try:
            db.execute(text("SELECT 1"))
            db_ok = True
        finally:
            db.close()
    except Exception:
        db_ok = False

    try:
        redis_ok = bool(redis_client.ping())
    except Exception:
        redis_ok = False

    return {
        "status": "ok" if db_ok and redis_ok else "degraded",
        "database": db_ok,
        "redis": redis_ok,
    }
