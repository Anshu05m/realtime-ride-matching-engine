from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Central application configuration, loaded from environment variables / .env."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = "postgresql+psycopg://ride_matching:ride_matching@localhost:5432/ride_matching"
    redis_url: str = "redis://localhost:6379/0"
    app_env: str = "development"
    database_echo: bool = False


settings = Settings()
