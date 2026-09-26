from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.models.driver import DriverStatus
from app.models.ride import RideStatus
from app.observability.metrics import metrics_tracker
from app.pricing.surge import surge_by_zone
from app.schemas.stats import StatsResponse
from app.schemas.surge import SurgeResponse
from app.storage.database import get_db
from app.storage.repositories.driver_repository import DriverRepository
from app.storage.repositories.ride_repository import ACTIVE_RIDE_STATUSES, RideRepository

router = APIRouter(tags=["stats"])


@router.get("/stats", response_model=StatsResponse, summary="Get live system statistics")
def get_stats(db: Session = Depends(get_db)) -> StatsResponse:
    driver_repo = DriverRepository(db)
    ride_repo = RideRepository(db)

    active_rides = sum(ride_repo.count_by_status(status) for status in ACTIVE_RIDE_STATUSES)
    metrics = metrics_tracker.snapshot()
    zones = {
        zone_id: SurgeResponse(
            zone_id=result.zone_id,
            demand=result.demand,
            supply=result.supply,
            multiplier=result.multiplier,
        )
        for zone_id, result in surge_by_zone(db).items()
    }

    return StatsResponse(
        available_drivers=driver_repo.count_by_status(DriverStatus.AVAILABLE),
        busy_drivers=driver_repo.count_by_status(DriverStatus.BUSY),
        active_rides=active_rides,
        completed_rides=ride_repo.count_by_status(RideStatus.COMPLETED),
        cancelled_rides=ride_repo.count_by_status(RideStatus.CANCELLED),
        failed_matches=metrics.failed_matches,
        match_throughput_per_minute=metrics.match_throughput_per_minute,
        p50_latency_ms=metrics.p50_latency_ms,
        p95_latency_ms=metrics.p95_latency_ms,
        p99_latency_ms=metrics.p99_latency_ms,
        surge_by_zone=zones,
    )
