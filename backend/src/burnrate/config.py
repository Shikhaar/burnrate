from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="BURNRATE_", env_file=".env", extra="ignore")

    database_url: str = "sqlite:///./burnrate.db"
    cors_origins: list[str] = ["http://localhost:3000"]
    # Optional bearer token protecting the read-only dashboard API. Empty = open (local/demo use).
    dashboard_token: str = ""
    max_batch_size: int = 1000
    # Slack-compatible incoming webhook for budget/anomaly alerts (POST {"text": ...}).
    alert_webhook_url: str = ""

    # Provider admin keys used by the billing connectors (never the app's inference keys).
    openai_admin_key: str = ""
    anthropic_admin_key: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
