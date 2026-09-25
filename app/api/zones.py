from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.pricing.surge import zone_surge
from app.schemas.surge import SurgeResponse
from app.storage.database import get_db

router = APIRouter(prefix="/zones", tags=["zones"])


@router.get("/{zone_id}/surge", response_model=SurgeResponse, summary="Get a zone's current surge")
def get_zone_surge(zone_id: str, db: Session = Depends(get_db)) -> SurgeResponse:
    result = zone_surge(db, zone_id)
    return SurgeResponse(
        zone_id=result.zone_id,
        demand=result.demand,
        supply=result.supply,
        multiplier=result.multiplier,
    )
