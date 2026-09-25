import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class RiderCreate(BaseModel):
    pickup_lat: float = Field(ge=-90, le=90)
    pickup_lng: float = Field(ge=-180, le=180)


class RiderResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    pickup_lat: float
    pickup_lng: float
    requested_at: datetime
