from pydantic import BaseModel


class StatsResponse(BaseModel):
    """Deliberately scoped to what's honestly and cheaply queryable from
    Postgres right now. No latency/throughput fields -- that instrumentation
    doesn't exist yet (Slice 8/9's job), and a null placeholder here would
    still imply a metric contract, which is closer to the fabrication
    CLAUDE.md warns against than simply not naming the field yet."""

    available_drivers: int
    busy_drivers: int
    active_rides: int
    completed_rides: int
    cancelled_rides: int
