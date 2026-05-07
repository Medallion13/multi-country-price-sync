"""
Application settings loaded from environment variables.

A single ``Settings`` instance is cached process-wide via ``get_settings``.
Use ``Settings`` inside tasks, services, and CLI entrypoints; never read
``os.environ`` directly.
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Strongli typed view of the runtime enviroment"""

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore", case_sensitive=False
    )

    # Postgres
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_db: str = "pipelines"
    postgres_user: str = "pipelines"
    postgres_password: str = "pipelines"

    # Prefect
    prefect_api_url: str = "http://localhost:4200/api"

    # External APIs
    fx_api_base: str = "https://open.er-api.com/v6"
    mock_woocommerce_base: str = "http://localhost:8000"

    # Pipeline tunables
    drift_threshold_pct: float = 5.0
    http_timeout_s: float = 10.0

    @property
    def database_url(self) -> str:
        """SQLAlchemy URL for the synchronous psycopg driver."""
        return (
            f"postgresql+psycopg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings instance."""
    return Settings()
