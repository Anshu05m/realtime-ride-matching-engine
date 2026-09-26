from pydantic import BaseModel

from app.schemas.surge import SurgeResponse


class StatsResponse(BaseModel):
    """available_drivers/busy_drivers/active_rides/completed_rides/
    cancelled_rides are durable Postgres counts, exactly like Slice 6.

    The remaining fields are Slice 8 additions, each with a real but
    different scope than the DB-backed ones above -- documented here rather
    than left to look like they all mean the same kind of thing:

    - failed_matches is an IN-PROCESS, LIFETIME counter, not a DB count --
      a failed match attempt (NoAvailableDriverError) leaves no persisted
      row anywhere, so unlike completed_rides/cancelled_rides, this resets
      to zero on every server restart.
    - match_throughput_per_minute is an exact count of successful matches in
      the trailing 60 real seconds, not an extrapolation.
    - p50/p95/p99_latency_ms are computed over a bounded rolling window of
      the most recent match_ride calls (settings.metrics_window_size), not
      all-time history -- consistent with surge's own rolling-window
      precedent (Slice 5), and necessary to keep memory bounded on a
      long-running server. Latency is measured around match_ride alone
      (candidate ranking through commit/lock-release), not create_ride's
      idempotency-check/commit time, and includes failed (unmatched)
      attempts -- a failed match still does real candidate-ranking/lock
      work.
    - surge_by_zone covers every zone with at least one ride requested in
      the current demand window (settings.surge_demand_window_seconds) --
      see app/pricing/surge.py's surge_by_zone().
    """

    available_drivers: int
    busy_drivers: int
    active_rides: int
    completed_rides: int
    cancelled_rides: int

    failed_matches: int
    match_throughput_per_minute: int
    p50_latency_ms: float
    p95_latency_ms: float
    p99_latency_ms: float
    surge_by_zone: dict[str, SurgeResponse]
