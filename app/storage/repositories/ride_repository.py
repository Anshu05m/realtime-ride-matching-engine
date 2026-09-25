import uuid
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.ride import Ride, RideStatus

# A driver/ride is "active" if it hasn't reached a terminal state yet. Slice 3 uses
# this set to enforce "a driver cannot have more than one active ride".
ACTIVE_RIDE_STATUSES = (RideStatus.REQUESTED, RideStatus.MATCHED, RideStatus.IN_PROGRESS)


class RideRepository:
    def __init__(self, db: Session):
        self.db = db

    def create(
        self,
        *,
        rider_id: uuid.UUID,
        pickup_lat: float,
        pickup_lng: float,
        idempotency_key: str | None = None,
        zone_id: str | None = None,
        surge_multiplier: float | None = None,
    ) -> Ride:
        ride = Ride(
            rider_id=rider_id,
            pickup_lat=pickup_lat,
            pickup_lng=pickup_lng,
            status=RideStatus.REQUESTED,
            idempotency_key=idempotency_key,
            zone_id=zone_id,
            surge_multiplier=surge_multiplier,
        )
        self.db.add(ride)
        self.db.flush()
        return ride

    def get_by_id(self, ride_id: uuid.UUID) -> Ride | None:
        return self.db.get(Ride, ride_id)

    def get_by_idempotency_key(self, idempotency_key: str) -> Ride | None:
        # Intentionally unscoped by status: a cancelled/completed ride is
        # still the correct thing to return for a retried key -- see
        # app/services/ride_service.py.
        stmt = select(Ride).where(Ride.idempotency_key == idempotency_key)
        return self.db.scalars(stmt).first()

    def count_recent_active_in_zone(self, zone_id: str, *, since: datetime) -> int:
        """Demand count for Slice 5's surge formula: rides requested in this
        zone since `since` that are still in an active status. The status
        filter matters -- a pure time-window count would double-count rides
        that already completed and freed their driver back into supply,
        overstating how squeezed the zone actually is."""
        stmt = (
            select(func.count())
            .select_from(Ride)
            .where(
                Ride.zone_id == zone_id,
                Ride.created_at >= since,
                Ride.status.in_(ACTIVE_RIDE_STATUSES),
            )
        )
        return self.db.scalar(stmt) or 0

    def list_active_for_driver(self, driver_id: uuid.UUID) -> list[Ride]:
        stmt = select(Ride).where(
            Ride.driver_id == driver_id, Ride.status.in_(ACTIVE_RIDE_STATUSES)
        )
        return list(self.db.scalars(stmt))

    def update_status(self, ride_id: uuid.UUID, *, status: RideStatus) -> Ride | None:
        ride = self.get_by_id(ride_id)
        if ride is None:
            return None
        ride.status = status
        self.db.flush()
        return ride
