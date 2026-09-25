from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.models.driver import DriverStatus
from app.models.ride import RideStatus
from app.schemas.stats import StatsResponse
from app.storage.database import get_db
from app.storage.repositories.driver_repository import DriverRepository
from app.storage.repositories.ride_repository import ACTIVE_RIDE_STATUSES, RideRepository

router = APIRouter(tags=["stats"])


@router.get("/stats", response_model=StatsResponse, summary="Get live system statistics")
def get_stats(db: Session = Depends(get_db)) -> StatsResponse:
    driver_repo = DriverRepository(db)
    ride_repo = RideRepository(db)

    active_rides = sum(ride_repo.count_by_status(status) for status in ACTIVE_RIDE_STATUSES)

    return StatsResponse(
        available_drivers=driver_repo.count_by_status(DriverStatus.AVAILABLE),
        busy_drivers=driver_repo.count_by_status(DriverStatus.BUSY),
        active_rides=active_rides,
        completed_rides=ride_repo.count_by_status(RideStatus.COMPLETED),
        cancelled_rides=ride_repo.count_by_status(RideStatus.CANCELLED),
    )
