import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.driver import Driver, DriverStatus


class DriverRepository:
    """Thin data-access wrapper around the drivers table. No matching/locking logic
    lives here — this slice is persistence only."""

    def __init__(self, db: Session):
        self.db = db

    def create(
        self,
        *,
        current_lat: float,
        current_lng: float,
        h3_index: str,
        status: DriverStatus = DriverStatus.AVAILABLE,
    ) -> Driver:
        driver = Driver(
            current_lat=current_lat,
            current_lng=current_lng,
            h3_index=h3_index,
            status=status,
        )
        self.db.add(driver)
        self.db.flush()
        return driver

    def get_by_id(self, driver_id: uuid.UUID) -> Driver | None:
        return self.db.get(Driver, driver_id)

    def list_by_status(self, status: DriverStatus) -> list[Driver]:
        stmt = select(Driver).where(Driver.status == status)
        return list(self.db.scalars(stmt))

    def update_location(
        self, driver_id: uuid.UUID, *, lat: float, lng: float, h3_index: str
    ) -> Driver | None:
        driver = self.get_by_id(driver_id)
        if driver is None:
            return None
        driver.current_lat = lat
        driver.current_lng = lng
        driver.h3_index = h3_index
        self.db.flush()
        return driver

    def update_status(self, driver_id: uuid.UUID, *, status: DriverStatus) -> Driver | None:
        driver = self.get_by_id(driver_id)
        if driver is None:
            return None
        driver.status = status
        self.db.flush()
        return driver
