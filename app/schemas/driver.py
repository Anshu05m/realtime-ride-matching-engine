import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.driver import DriverStatus


class DriverCreate(BaseModel):
    current_lat: float = Field(ge=-90, le=90)
    current_lng: float = Field(ge=-180, le=180)


class DriverLocationUpdate(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)


class DriverResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    current_lat: float
    current_lng: float
    h3_index: str
    zone_id: str
    status: DriverStatus
    last_updated_at: datetime
