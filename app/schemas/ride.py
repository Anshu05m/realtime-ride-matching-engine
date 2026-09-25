import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.ride import RideStatus


class RideCreateRequest(BaseModel):
    rider_id: uuid.UUID
    pickup_lat: float = Field(ge=-90, le=90)
    pickup_lng: float = Field(ge=-180, le=180)


class RideResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    rider_id: uuid.UUID
    driver_id: uuid.UUID | None
    status: RideStatus
    pickup_lat: float
    pickup_lng: float
    matched_at: datetime | None
    fare: float | None
    surge_multiplier: float | None
    zone_id: str | None
    created_at: datetime
    updated_at: datetime
