from pydantic import BaseModel


class SurgeResponse(BaseModel):
    zone_id: str
    demand: int
    supply: int
    multiplier: float
