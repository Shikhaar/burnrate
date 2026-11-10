from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="BURNRATE_", env_file=".env", extra="ignore")

    database_url: str = "sqlite:///./burnrate.db"
    cors_origins: list[str] = ["http://localhost:3000"]
    # Bearer token protecting the dashboard API (/api/*, including budget/alert writes). Empty = open; local demo only.
    dashboard_token: str = ""
    max_batch_size: int = 1000
    # Logging: "text" for local dev, "json" for production log pipelines.
    log_format: str = "text"
    log_level: str = "INFO"
    # Token-bucket limits (requests/minute, also the burst size). 0 disables.
    ingest_rate_limit_per_minute: int = 1200  # per ingest API key (SDKs batch, so this is generous)
    api_rate_limit_per_minute: int = 600  # per client IP on the dashboard API
    # Share rate limits across API replicas, e.g. redis://redis:6379/0. Empty = per-process limits.
    redis_url: str = ""

    # Slack-compatible incoming webhook for budget/anomaly alerts (POST {"text": ...}).
    alert_webhook_url: str = ""

    # Provider admin keys used by `burnrate sync` (never the app's inference keys). Only read-only endpoints are
    # called, but the keys can manage the account: give them to the sync job only, not the API service.
    openai_admin_key: str = ""
    anthropic_admin_key: str = ""

    # AI coding tools imported as usage events (see `burnrate sync cursor|copilot`).
    cursor_admin_key: str = ""
    # Token for an org owner or billing manager; reads the org's billing usage report.
    github_token: str = ""
    github_org: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
