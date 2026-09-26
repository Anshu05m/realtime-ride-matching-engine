from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Central application configuration, loaded from environment variables / .env."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = "postgresql+psycopg://ride_matching:ride_matching@localhost:5432/ride_matching"
    redis_url: str = "redis://localhost:6379/0"
    app_env: str = "development"
    database_echo: bool = False

    # H3 resolution for driver/rider cell indexing. Resolution 8 has an average
    # hexagon edge length of ~0.53km — fine-grained enough that candidate sets stay
    # small in a dense simulated city, coarse enough that a small ring radius covers
    # a realistic pickup search area.
    h3_resolution: int = 8

    # Candidate search (Slice 2, no locking yet): how many available drivers to try
    # to find before stopping ring expansion, and how many rings to search before
    # giving up.
    match_target_candidates: int = 5
    match_max_ring: int = 3

    # Slice 3: TTL for the Redis distributed lock held around a single driver's
    # assignment critical section. Long enough to cover a normal DB round-trip,
    # short enough that a crashed worker's lock clears in a reasonable time. See
    # app/redis/lock.py and INTERVIEW_PREP.md for the tradeoff this represents.
    lock_ttl_ms: int = 5000

    # Slice 5: H3 resolution for surge-pricing zones -- deliberately coarser
    # than h3_resolution (matching), and NOT derived from it. Matching's cells
    # are fine-grained on purpose (small candidate sets); reusing that
    # resolution for pricing would make surge flicker to near-max whenever a
    # single driver crosses a tiny cell boundary. Resolution 6 is ~49x the
    # area of resolution 8 (H3 gets ~7x coarser per level), giving each zone a
    # more stable pool of drivers/requests to aggregate over.
    surge_zone_resolution: int = 6

    # How far back (in seconds) to count "recent" ride requests as demand for
    # a zone. A rolling window, not an all-time count -- old demand shouldn't
    # keep inflating surge forever.
    surge_demand_window_seconds: int = 300

    # Surge formula: multiplier = clamp(1.0 + surge_sensitivity * ratio, 1.0,
    # surge_max_multiplier), where ratio = demand / max(supply, 1). Continuous
    # and capped rather than a step table -- see INTERVIEW_PREP.md for why.
    surge_sensitivity: float = 0.5
    surge_max_multiplier: float = 3.0

    # Slice 8: max in-flight dashboard events buffered between the sync
    # publishers (matcher.py, ride_service.py) and the async WebSocket
    # broadcast loop. Bounded and non-blocking on purpose -- if nothing is
    # consuming (no dashboard connected, or between app lifespans in tests),
    # publish() must never block a request or grow without limit; a full
    # queue just drops the newest event, logged at WARNING.
    dashboard_event_queue_size: int = 1000

    # Slice 8: how many recent match_ride outcomes GET /stats's p50/p95/p99
    # latency figures are computed over. A rolling window, not an all-time
    # history -- the same "bounded, not all-time" precedent surge's demand
    # window (surge_demand_window_seconds) already set in Slice 5, needed
    # here to keep memory bounded on a long-running server.
    metrics_window_size: int = 500


settings = Settings()
