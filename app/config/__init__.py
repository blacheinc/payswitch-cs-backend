"""Application configuration.

Values are loaded from environment variables (and an optional ``.env`` file in
development). In production, sensitive values are expected to be provided by
the deploy layer — either injected directly as environment variables
(connection-string style) or materialized from Azure Key Vault at startup by
the custom settings source (see ``app/config/keyvault_source.py``).

No production-realistic defaults live in this file. Anything that would leak
our infrastructure into a client deployment is either a blank string or
raises at boot time via :meth:`Settings.validate_production`.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any, Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict

from app.config.keyvault_source import KeyVaultSettingsSource


class Settings(BaseSettings):
    """Central configuration for the application."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """Insert the Key Vault source between init-kwargs and env vars.

        Resolution order (highest priority first):

        1. Values passed directly to ``Settings(...)`` — overrides for tests.
        2. Azure Key Vault — only when ``AZURE_KEYVAULT_URL`` + SP creds
           are all configured; silently a no-op otherwise.
        3. Environment variables — the normal runtime source.
        4. ``.env`` file — development convenience.
        5. Docker/Linux file secrets — unused today but left in the chain.
        """
        return (
            init_settings,
            KeyVaultSettingsSource(settings_cls),
            env_settings,
            dotenv_settings,
            file_secret_settings,
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
    # Dev default points at the docker-compose postgres. Overridden in every
    # non-dev environment.
    database_url: str = (
        "postgresql+asyncpg://postgres:postgres@localhost:5432/credit_scoring"
    )
    database_pool_size: int = 20
    database_max_overflow: int = 10

    # ── Redis ────────────────────────────────────────────────────────────────
    redis_url: str = "redis://localhost:6379/0"

    # ── Azure Storage (general-purpose blob containers) ──────────────────────
    azure_storage_connection_string: str = ""
    azure_storage_container_bulk: str = "bulk-uploads"
    azure_storage_container_training: str = "training-data"
    azure_storage_container_reports: str = "reports"

    # ── Champion Model Artifacts Storage ─────────────────────────────────────
    # Separate storage account for model artifacts. May point at the same
    # account as ``azure_storage_connection_string`` or a dedicated one.
    champion_storage_connection_string: str = ""
    champion_storage_container: str = "model-artifacts"

    # ── Orchestrator Function App (rule evaluation) ──────────────────────────
    # URL is blank by default — set per environment. Function key is a secret.
    orchestrator_base_url: str = ""
    orchestrator_function_key: str = ""

    # ── DE Inference Function App (ML scoring) ───────────────────────────────
    de_inference_url: str = ""
    de_inference_code: str = ""

    # ── Customer Service Function App ────────────────────────────────────────
    customer_service_base_url: str = ""
    customer_service_function_key: str = ""

    # ── Azure Key Vault (optional; secrets fall back to env vars when unset) ─
    azure_keyvault_url: str = ""
    # Service principal used to authenticate to Key Vault. Required only when
    # ``azure_keyvault_url`` is set; otherwise ignored.
    azure_tenant_id: str = ""
    azure_client_id: str = ""
    azure_client_secret: str = ""

    # ── Azure Service Bus ────────────────────────────────────────────────────
    azure_servicebus_connection_string: str = ""
    azure_servicebus_queue_scoring: str = "scoring-jobs"
    azure_servicebus_queue_webhooks: str = "webhook-deliveries"

    # Service Bus topics and subscriptions (ML pipeline contract)
    azure_servicebus_topic_training_started: str = "model-training-started"
    azure_servicebus_topic_training_completed: str = "model-training-completed"
    azure_servicebus_topic_scoring_complete: str = "scoring-complete"
    azure_servicebus_topic_score_applicant: str = "score-applicant"
    azure_servicebus_topic_batch_score_request: str = "batch-score-request"
    azure_servicebus_topic_batch_score_complete: str = "batch-score-complete"
    azure_servicebus_sub_backend: str = "backend-sub"
    azure_servicebus_sub_cs_backend: str = "cs-backend"

    # ── XDS Credit Bureau ────────────────────────────────────────────────────
    xds_username: str = ""
    xds_password: str = ""
    xds_base_url: str = "https://www.online.xdsdata.com/XdsDataRestApi/Api"
    xds_timeout_seconds: int = 30

    # ── JWT ───────────────────────────────────────────────────────────────────
    # No default — production deploy MUST set this. Development can rely on
    # ``.env`` or the auto-generated value from the docker-compose bring-up.
    jwt_secret_key: str = ""
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 15
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
    # Contact address shown as the "From" in outbound emails. Must be owned
    # and verified by the client in their email provider.
    email_from: str = ""

    # ── Dashboard (frontend) ─────────────────────────────────────────────────
    # Used to build password-reset / login-continuation links in transactional
    # emails. Example: "https://dashboard.example.com".
    dashboard_base_url: str = ""

    # ── Bootstrap admin (first-run seeder only) ──────────────────────────────
    # Optional convenience for fresh deployments. When set, the bootstrap
    # script creates this admin on first boot, and the operator is expected
    # to log in once and rotate the password immediately.
    bootstrap_admin_email: str = ""
    bootstrap_admin_password: str = ""
    bootstrap_admin_name: str = "Platform Administrator"

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

    @property
    def use_keyvault(self) -> bool:
        """Key Vault is active only when the URL *and* all SP creds are set.

        Anything less than the full four-tuple results in the Key Vault
        source short-circuiting to a no-op (with a WARNING log) and env
        vars taking over — this property reports the same truth so the
        startup banner doesn't lie.
        """
        return bool(
            self.azure_keyvault_url
            and self.azure_tenant_id
            and self.azure_client_id
            and self.azure_client_secret
        )

    # ── Production boot validation ───────────────────────────────────────────
    # Required fields per environment. Non-production environments get a
    # broad pass so local dev and integration tests aren't held hostage.
    _REQUIRED_IN_PRODUCTION: tuple[str, ...] = (
        "database_url",
        "redis_url",
        "jwt_secret_key",
        "azure_servicebus_connection_string",
        "azure_storage_connection_string",
        "orchestrator_base_url",
        "orchestrator_function_key",
        "de_inference_url",
        "de_inference_code",
        "xds_username",
        "xds_password",
        "dashboard_base_url",
        "email_from",
    )

    def validate_production(self) -> None:
        """Raise ``RuntimeError`` if any required production setting is missing.

        Called from the FastAPI ``lifespan`` startup hook so a misconfigured
        container fails fast with a readable error rather than silently
        running with placeholder values.
        """
        if not self.is_production:
            return

        missing: list[str] = []
        for name in self._REQUIRED_IN_PRODUCTION:
            value = getattr(self, name, "")
            if isinstance(value, str) and not value.strip():
                missing.append(name)

        # jwt_secret_key must also not be the old placeholder — guard against
        # an older deploy that still ships the legacy literal.
        if self.jwt_secret_key.strip().startswith("CHANGE-ME"):
            missing.append("jwt_secret_key (placeholder value)")

        if missing:
            raise RuntimeError(
                "Production configuration missing required settings: "
                + ", ".join(sorted(set(missing)))
                + ". Set them via environment variables or Azure Key Vault."
            )


@lru_cache
def get_settings() -> Settings:
    """Cached settings instance (created once per process)."""
    return Settings()
