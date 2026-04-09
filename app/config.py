"""Application configuration loaded from environment variables and Azure Key Vault."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Central configuration for the application.

    Values are loaded from environment variables (and .env file in development).
    Sensitive values should be stored in Azure Key Vault and injected at deploy time.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── Application ──────────────────────────────────────────────────────────
    app_name: str = "PaySwitch Credit Scoring API"
    app_env: Literal["development", "staging", "production"] = "development"
    debug: bool = False
    api_v1_prefix: str = "/v1"
    allowed_origins: list[str] = Field(
        default_factory=lambda: ["http://localhost:3000"]
    )

    @field_validator("allowed_origins", mode="before")
    @classmethod
    def parse_origins(cls, v: str | list[str]) -> list[str]:
        if isinstance(v, str):
            return [origin.strip() for origin in v.split(",")]
        return v

    # ── Server ───────────────────────────────────────────────────────────────
    host: str = "0.0.0.0"
    port: int = 8000

    # ── PostgreSQL ───────────────────────────────────────────────────────────
    database_url: str = (
        "postgresql+asyncpg://postgres:postgres@localhost:5432/credit_scoring"
    )
    database_pool_size: int = 20
    database_max_overflow: int = 10

    # ── Redis ────────────────────────────────────────────────────────────────
    redis_url: str = "redis://localhost:6379/0"

    # ── Azure Cosmos DB ──────────────────────────────────────────────────────
    cosmos_endpoint: str = ""
    cosmos_key: str = ""
    cosmos_database: str = "credit_scoring"

    # ── Azure Storage ────────────────────────────────────────────────────────
    azure_storage_connection_string: str = ""
    azure_storage_container_bulk: str = "bulk-uploads"
    azure_storage_container_training: str = "training-data"
    azure_storage_container_reports: str = "reports"

    # ── Azure Key Vault ──────────────────────────────────────────────────────
    azure_keyvault_url: str = ""

    # ── Azure Service Bus ────────────────────────────────────────────────────
    azure_servicebus_connection_string: str = ""
    azure_servicebus_queue_scoring: str = "scoring-jobs"
    azure_servicebus_queue_webhooks: str = "webhook-deliveries"

    # Service Bus topics and subscriptions (new ML pipeline contract)
    azure_servicebus_topic_training_started: str = "model-training-started"
    azure_servicebus_topic_training_completed: str = "model-training-completed"
    azure_servicebus_topic_scoring_complete: str = "scoring-complete"
    # BE → DE: stub topic for triggering scoring (swap once DE team confirms)
    azure_servicebus_topic_score_applicant: str = "score-applicant"
    azure_servicebus_sub_backend: str = "backend-sub"
    azure_servicebus_sub_cs_backend: str = "cs-backend"

    # DE inference Azure Function
    de_inference_url: str = "https://blache-cdtscr-dev-transform-y27jgavel2x32.azurewebsites.net/api/transform/inference"
    de_inference_code: str = ""

    # ── XDS Credit Bureau ────────────────────────────────────────────────────
    xds_username: str = ""
    xds_password: str = ""
    xds_base_url: str = "https://www.online.xdsdata.com/XdsDataRestApi/Api"
    xds_timeout_seconds: int = 30

    # ── JWT ───────────────────────────────────────────────────────────────────
    jwt_secret_key: str = "CHANGE-ME-in-production-use-keyvault"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 1
    refresh_token_expire_days: int = 7

    # ── API Key Settings ─────────────────────────────────────────────────────
    api_key_prefix_test: str = "sk_test_"
    api_key_prefix_live: str = "sk_live_"

    # ── Rate Limiting ────────────────────────────────────────────────────────
    rate_limit_per_minute: int = 100
    rate_limit_per_day: int = 50_000
    rate_limit_burst: int = 200

    # ── Scoring ──────────────────────────────────────────────────────────────
    scoring_timeout_seconds: int = 5
    score_validity_days: int = 14

    # ── Email ────────────────────────────────────────────────────────────────
    email_provider: Literal["console", "smtp", "sendgrid", "resend"] = "console"
    resend_api_key: str = ""
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    email_from: str = "noreply@creditscoring.payswitch.com.gh"

    # ── Helpers ──────────────────────────────────────────────────────────────

    @property
    def is_development(self) -> bool:
        return self.app_env == "development"

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @property
    def use_service_bus(self) -> bool:
        """Use in-memory queue if Service Bus connection string is empty."""
        return bool(self.azure_servicebus_connection_string)


@lru_cache
def get_settings() -> Settings:
    """Cached settings instance (created once per process)."""
    return Settings()
