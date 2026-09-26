import time
import uuid

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.orm import Session

from app.matching.matcher import NoAvailableDriverError, match_ride
from app.models.ride import RideStatus
from app.observability.metrics import metrics_tracker
from app.schemas.errors import ErrorResponse
from app.schemas.ride import RideCreateRequest, RideResponse
from app.services.ride_service import cancel_ride, complete_ride, create_ride
from app.storage.database import get_db
from app.storage.repositories.ride_repository import RideRepository

router = APIRouter(prefix="/rides", tags=["rides"])


@router.post(
    "",
    response_model=RideResponse,
    status_code=201,
    responses={409: {"model": ErrorResponse}},
    summary="Request a ride",
)
def request_ride(
    payload: RideCreateRequest,
    db: Session = Depends(get_db),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key", max_length=255),
) -> RideResponse:
    """Creates the ride, then immediately attempts to match a driver in the
    same request. A ride with no driver found is still a valid 201 response
    -- "no driver available right now" is a normal outcome of this system,
    not a client error (see app/matching/matcher.py's NoAvailableDriverError).

    Both create_ride and match_ride own their own commits (see their module
    docstrings for why); this route never calls db.commit() itself -- it's
    pure orchestration with no transaction-boundary responsibility of its
    own.

    Slice 8: wall-clock time around the match_ride call alone (not
    create_ride+match_ride together) is recorded into metrics_tracker for
    GET /stats's p50/p95/p99 latency figures -- see
    app/observability/metrics.py's module docstring for why latency is
    scoped to match_ride specifically, and why a failed match (unmatched) is
    still part of the distribution, not excluded from it.
    """
    key = idempotency_key or None  # an empty header value isn't a real key
    ride = create_ride(
        db,
        rider_id=payload.rider_id,
        pickup_lat=payload.pickup_lat,
        pickup_lng=payload.pickup_lng,
        idempotency_key=key,
    )

    if ride.status == RideStatus.REQUESTED:
        # Only attempt matching for a ride that's actually still REQUESTED --
        # true for a fresh ride, and correctly skips re-matching an
        # idempotent-replay hit that already progressed past REQUESTED (which
        # would otherwise hit match_ride's own precondition ValueError).
        start = time.perf_counter()
        try:
            ride = match_ride(db, ride)
        except NoAvailableDriverError:
            metrics_tracker.record("unmatched", time.perf_counter() - start)
            # valid outcome: ride stays REQUESTED, still a 201
        except Exception:
            metrics_tracker.record("error", time.perf_counter() - start)
            raise
        else:
            metrics_tracker.record("matched", time.perf_counter() - start)

    return ride


@router.get(
    "/{ride_id}",
    response_model=RideResponse,
    responses={404: {"model": ErrorResponse}},
    summary="Get a ride's current status",
)
def get_ride(ride_id: uuid.UUID, db: Session = Depends(get_db)) -> RideResponse:
    ride = RideRepository(db).get_by_id(ride_id)
    if ride is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "ride_not_found", "message": f"ride {ride_id} not found"},
        )
    return ride


@router.post(
    "/{ride_id}/cancel",
    response_model=RideResponse,
    responses={404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}},
    summary="Cancel a ride",
)
def cancel_ride_endpoint(ride_id: uuid.UUID, db: Session = Depends(get_db)) -> RideResponse:
    return cancel_ride(db, ride_id)


@router.post(
    "/{ride_id}/complete",
    response_model=RideResponse,
    responses={404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}},
    summary="Complete a ride",
)
def complete_ride_endpoint(ride_id: uuid.UUID, db: Session = Depends(get_db)) -> RideResponse:
    return complete_ride(db, ride_id)
