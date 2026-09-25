from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.schemas.rider import RiderCreate, RiderResponse
from app.storage.database import get_db
from app.storage.repositories.rider_repository import RiderRepository

router = APIRouter(prefix="/riders", tags=["riders"])


@router.post("", response_model=RiderResponse, status_code=201, summary="Create a rider")
def create_rider(payload: RiderCreate, db: Session = Depends(get_db)) -> RiderResponse:
    rider = RiderRepository(db).create(
        pickup_lat=payload.pickup_lat, pickup_lng=payload.pickup_lng
    )
    db.commit()
    db.refresh(rider)
    return rider
