"""Typed runtime configuration loaded from environment + .env file."""

from __future__ import annotations

from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Application
    app_env: Literal["development", "staging", "production"] = "development"
    log_level: str = "INFO"

    # Database. URL uses postgresql+psycopg:// so SQLAlchemy picks the
    # psycopg3 driver (we install psycopg[binary], not psycopg2).
    database_url: str = "postgresql+psycopg://taq:taq@localhost:5432/taquantgeo"

    # Redis
    redis_url: str = "redis://localhost:6379/0"

    # AIS — live
    aisstream_api_key: str = ""

    # AIS — historical (BigQuery / GFW)
    google_application_credentials: str = ""
    gcp_project_id: str = ""
    gfw_api_token: str = ""

    # Object storage (Cloudflare R2)
    r2_account_id: str = ""
    r2_access_key_id: str = ""
    r2_secret_access_key: str = ""
    r2_bucket: str = "taquantgeo-archive"
    r2_endpoint: str = ""

    # Equity prices
    polygon_api_key: str = ""

    # Interactive Brokers
    ibkr_host: str = "127.0.0.1"
    ibkr_port: int = 7497
    ibkr_client_id: int = 1
    ibkr_account: str = ""

    # Risk limits — required for live trading
    max_position_usd: int = 10000
    max_gross_exposure_usd: int = 50000
    daily_loss_limit_usd: int = 2000
    kill_switch: bool = False

    # Notifications
    discord_webhook_url: str = ""
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_from: str = ""
    alert_phone: str = ""

    # Observability
    sentry_dsn: str = ""

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"


settings = Settings()
