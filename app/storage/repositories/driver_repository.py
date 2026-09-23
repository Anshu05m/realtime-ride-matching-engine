import uuid
from collections.abc import Iterable

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

    def get_by_id(self, driver_id: uuid.UUID, *, fresh: bool = False) -> Driver | None:
        """By default, Session.get() returns a cached object from this session's
        identity map if one is already loaded for this id -- no new SELECT is
        issued, even if another session has since committed a change to that
        row. That's normally a reasonable/fast default, but it's wrong for a
        lock-protected re-check (see app/matching/matcher.py), where the whole
        point is to see the row's true current state. Pass fresh=True there to
        force a real SELECT via populate_existing, bypassing the cache."""
        if fresh:
            return self.db.get(Driver, driver_id, populate_existing=True)
        return self.db.get(Driver, driver_id)

    def list_by_status(self, status: DriverStatus) -> list[Driver]:
        stmt = select(Driver).where(Driver.status == status)
        return list(self.db.scalars(stmt))

    def list_available_in_cells(self, cells: Iterable[str]) -> list[Driver]:
        """Candidate-discovery query for Slice 2's matching: available drivers
        whose h3_index falls in one of the given cells. `cells` is normally a
        single H3 ring's worth of cell addresses (see matching/candidate_search.py),
        so this stays a cheap indexed IN-query, never a full table scan."""
        cells = list(cells)
        if not cells:
            return []
        stmt = select(Driver).where(
            Driver.status == DriverStatus.AVAILABLE, Driver.h3_index.in_(cells)
        )
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
