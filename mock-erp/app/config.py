"""Runtime settings, read from environment variables."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "sqlite+pysqlite:///:memory:"
    log_level: str = "INFO"
    # Shared secret callers must send in X-API-Key. Empty string disables the check (tests).
    api_key: str = "erp-demo-key"
    default_currency: str = "RWF"


@lru_cache
def get_settings() -> Settings:
    return Settings()
