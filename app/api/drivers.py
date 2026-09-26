import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.config import settings
from app.matching.geo import latlng_to_h3
from app.models.driver import DriverStatus
from app.pricing.zones import zone_id_for_point
from app.schemas.driver import DriverCreate, DriverLocationUpdate, DriverResponse
from app.schemas.errors import ErrorResponse
from app.storage.database import get_db
from app.storage.repositories.driver_repository import DriverRepository

router = APIRouter(prefix="/drivers", tags=["drivers"])


@router.get("", response_model=list[DriverResponse], summary="List drivers")
def list_drivers(
    status: DriverStatus | None = None, db: Session = Depends(get_db)
) -> list[DriverResponse]:
    """Slice 8: powers the dashboard's initial map load and periodic
    refresh -- unfiltered by default, or scoped with ?status= for symmetry
    with the repository's existing list_by_status."""
    repo = DriverRepository(db)
    if status is not None:
        return repo.list_by_status(status)
    return repo.list_all()


def _not_found(driver_id: uuid.UUID) -> HTTPException:
    return HTTPException(
        status_code=404,
        detail={"code": "driver_not_found", "message": f"driver {driver_id} not found"},
    )


@router.post("", response_model=DriverResponse, status_code=201, summary="Create a driver")
def create_driver(payload: DriverCreate, db: Session = Depends(get_db)) -> DriverResponse:
    """Creates a driver at the given location. h3_index and zone_id are
    computed here from the coordinates, not accepted from the client --
    they're derived values, not independently meaningful input (same
    convention repositories already use: callers compute, repos stay dumb)."""
    h3_index = latlng_to_h3(payload.current_lat, payload.current_lng, settings.h3_resolution)
    zone_id = zone_id_for_point(
        payload.current_lat, payload.current_lng, settings.surge_zone_resolution
    )
    driver = DriverRepository(db).create(
        current_lat=payload.current_lat,
        current_lng=payload.current_lng,
        h3_index=h3_index,
        zone_id=zone_id,
    )
    db.commit()
    db.refresh(driver)
    return driver


@router.get(
    "/{driver_id}",
    response_model=DriverResponse,
    responses={404: {"model": ErrorResponse}},
    summary="Get a driver's current state",
)
def get_driver(driver_id: uuid.UUID, db: Session = Depends(get_db)) -> DriverResponse:
    driver = DriverRepository(db).get_by_id(driver_id)
    if driver is None:
        raise _not_found(driver_id)
    return driver


@router.patch(
    "/{driver_id}/location",
    response_model=DriverResponse,
    responses={404: {"model": ErrorResponse}},
    summary="Update a driver's location",
)
def update_driver_location(
    driver_id: uuid.UUID, payload: DriverLocationUpdate, db: Session = Depends(get_db)
) -> DriverResponse:
    h3_index = latlng_to_h3(payload.lat, payload.lng, settings.h3_resolution)
    zone_id = zone_id_for_point(payload.lat, payload.lng, settings.surge_zone_resolution)
    driver = DriverRepository(db).update_location(
        driver_id, lat=payload.lat, lng=payload.lng, h3_index=h3_index, zone_id=zone_id
    )
    if driver is None:
        raise _not_found(driver_id)
    db.commit()
    db.refresh(driver)
    return driver
