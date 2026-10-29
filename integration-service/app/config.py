"""Runtime settings, read from environment variables."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "sqlite+pysqlite:///:memory:"
    log_level: str = "INFO"
    # partner-facing API key (X-API-Key). Empty disables the check (tests).
    api_key: str = "partner-demo-key"

    fineract_base_url: str = "http://localhost:8080/fineract-provider/api/v1"
    fineract_tenant_id: str = "default"
    fineract_username: str = "mifos"
    fineract_password: str = "password"
    fineract_connect_timeout_s: float = 3.0
    # Admin credentials used ONLY by `python -m app.setup` (Part B). Runtime calls use the
    # least-privilege API user above, which setup creates.
    fineract_admin_username: str = "mifos"
    fineract_admin_password: str = "password"
    # Part B configuration
    office_name: str = "Kigali Main Office"
    currency_code: str = "RWF"
    loan_product_name: str = "SME Asset Finance"
    loan_product_short_name: str = "SMEA"
    api_role_name: str = "integration-service"
    api_user_username: str = "integration-api"
    api_user_password: str = "Integr8tion#2026"
    fineract_read_timeout_s: float = 15.0

    erp_base_url: str = "http://localhost:8101"
    erp_api_key: str = "erp-demo-key"
    erp_connect_timeout_s: float = 3.0
    erp_read_timeout_s: float = 10.0

    # transient-failure retry inside one sync call: attempts and base backoff (doubles each time)
    sync_max_attempts: int = 3
    sync_backoff_s: float = 0.5


@lru_cache
def get_settings() -> Settings:
    return Settings()
