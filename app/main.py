from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy import text
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api import drivers, riders, rides, stats, zones
from app.models.ride import InvalidRideStateError, RideNotFoundError
from app.redis.client import redis_client
from app.services.ride_service import IdempotencyKeyConflictError
from app.storage.database import SessionLocal

app = FastAPI(
    title="Real-Time Ride-Matching Engine",
    description=(
        "A real-time ride-matching backend demonstrating geospatial matching, "
        "distributed locking, idempotency, and surge pricing. Domain "
        "endpoints wrap the matching/idempotency/pricing logic built in "
        "Slices 2-5; see /docs for the full API."
    ),
)

app.include_router(drivers.router)
app.include_router(riders.router)
app.include_router(rides.router)
app.include_router(zones.router)
app.include_router(stats.router)


# --- Structured error envelope -----------------------------------------
#
# Every error response, regardless of source, comes back as
# {"error": {"code": "...", "message": "..."}} -- plain, visible handler
# functions, no hidden middleware magic. Registered against the Starlette
# base HTTPException (not fastapi.HTTPException) so this also catches
# framework-level errors, like an unmatched route's default 404, that
# FastAPI itself raises as the Starlette class.


def _error_response(status_code: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"error": {"code": code, "message": message}})


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    return _error_response(422, "validation_error", str(exc))


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    detail = exc.detail
    if isinstance(detail, dict) and "code" in detail and "message" in detail:
        return _error_response(exc.status_code, detail["code"], detail["message"])
    return _error_response(exc.status_code, "http_error", str(detail))


@app.exception_handler(IdempotencyKeyConflictError)
async def idempotency_conflict_handler(
    request: Request, exc: IdempotencyKeyConflictError
) -> JSONResponse:
    return _error_response(409, "idempotency_key_conflict", str(exc))


@app.exception_handler(RideNotFoundError)
async def ride_not_found_handler(request: Request, exc: RideNotFoundError) -> JSONResponse:
    return _error_response(404, "ride_not_found", str(exc))


@app.exception_handler(InvalidRideStateError)
async def invalid_ride_state_handler(request: Request, exc: InvalidRideStateError) -> JSONResponse:
    return _error_response(409, "invalid_ride_state", str(exc))


@app.get("/health")
def health() -> dict:
    """Confirms the two durable dependencies (Postgres, Redis) are reachable."""
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
