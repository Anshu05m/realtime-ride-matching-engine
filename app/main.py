import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api import drivers, riders, rides, stats, websocket, zones
from app.config import settings
from app.models.ride import InvalidRideStateError, RideNotFoundError
from app.observability.events import event_bus
from app.redis.client import redis_client
from app.redis.lock import LockingUnavailableError
from app.services.ride_service import IdempotencyKeyConflictError
from app.storage.database import SessionLocal

logger = logging.getLogger(__name__)


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Slice 8: the project's first lifespan hook -- attaches the dashboard
    event bus to this process's running event loop and starts its broadcast
    consumer task (see app/observability/events.py's module docstring for
    why attach/detach is per-lifespan, not once at import time). Shutdown
    cancels the task and awaits it -- a real coroutine `await` on
    asyncio.Queue.get() is natively cancellable, so this never hangs or
    leaks a thread, unlike the blocking-queue bridge design it replaced."""
    event_bus.attach(asyncio.get_running_loop(), maxsize=settings.dashboard_event_queue_size)
    broadcast_task = asyncio.create_task(event_bus.run_broadcast_loop())
    try:
        yield
    finally:
        broadcast_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await broadcast_task
        event_bus.detach()


app = FastAPI(
    title="Real-Time Ride-Matching Engine",
    description=(
        "A real-time ride-matching backend demonstrating geospatial matching, "
        "distributed locking, idempotency, and surge pricing. Domain "
        "endpoints wrap the matching/idempotency/pricing logic built in "
        "Slices 2-5; see /docs for the full API."
    ),
    lifespan=lifespan,
)

app.include_router(drivers.router)
app.include_router(riders.router)
app.include_router(rides.router)
app.include_router(zones.router)
app.include_router(stats.router)
app.include_router(websocket.router)

# Slice 8: the static dashboard (dashboard/ at the project root, a sibling of
# app/ -- see CLAUDE.md's project structure), served at /dashboard/. Mounted
# last so it never shadows an API route.
app.mount("/dashboard", StaticFiles(directory="dashboard", html=True), name="dashboard")


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


# Slice 9: two dependency-unavailable handlers, both 503 (the dependency is
# temporarily unreachable/overloaded, not that our own code has a bug) --
# found while writing this slice's chaos tests, both previously propagated
# as an unhandled, unstructured 500, contradicting this file's own envelope
# invariant above. Both return a FIXED, generic client-facing message (not
# str(exc)) -- unlike the domain exceptions above, a raw DB/Redis driver
# error can contain internal detail (SQL fragments, connection info) that
# has no business being a stable, public API response. The real exception
# is logged server-side instead.


@app.exception_handler(LockingUnavailableError)
async def locking_unavailable_handler(request: Request, exc: LockingUnavailableError) -> JSONResponse:
    logger.exception("locking unavailable", exc_info=exc)
    return _error_response(
        503, "locking_unavailable", "the matching coordination layer is temporarily unavailable"
    )


@app.exception_handler(SQLAlchemyError)
async def database_error_handler(request: Request, exc: SQLAlchemyError) -> JSONResponse:
    logger.exception("database error", exc_info=exc)
    return _error_response(503, "database_error", "a database error occurred, please retry")


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
