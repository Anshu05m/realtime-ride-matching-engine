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


settings = Settings()
