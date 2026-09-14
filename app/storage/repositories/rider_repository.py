import uuid

from sqlalchemy.orm import Session

from app.models.rider import Rider


class RiderRepository:
    def __init__(self, db: Session):
        self.db = db

    def create(self, *, pickup_lat: float, pickup_lng: float) -> Rider:
        rider = Rider(pickup_lat=pickup_lat, pickup_lng=pickup_lng)
        self.db.add(rider)
        self.db.flush()
        return rider

    def get_by_id(self, rider_id: uuid.UUID) -> Rider | None:
        return self.db.get(Rider, rider_id)
