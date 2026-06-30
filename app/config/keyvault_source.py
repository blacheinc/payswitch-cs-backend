"""Pydantic-settings source that resolves secrets from Azure Key Vault.

This source is **optional and additive**. It sits between the init-kwargs
and environment-variable sources in the settings chain:

    init kwargs  →  KeyVaultSource  →  env vars  →  .env  →  defaults

When ``AZURE_KEYVAULT_URL`` is unset (or the SP creds are incomplete) the
source is a no-op — every field falls through to the normal env/.env
layers. This gives us the "either-or" behaviour the project brief asks
for: Key Vault when configured, plain env vars otherwise.

Only fields enumerated in :data:`SECRET_FIELDS` are looked up in Key
Vault — everything else (feature flags, URLs, timeouts) stays env-only
to avoid pointless round-trips.

Naming convention
-----------------
A Python field ``jwt_secret_key`` maps to the Key Vault secret name
``jwt-secret-key``. Azure Key Vault secret names must match
``^[0-9a-zA-Z-]+$``, so we replace underscores with hyphens.

Authentication
--------------
Authentication uses a service principal (client-id / client-secret /
tenant-id) read from environment variables — the same "static
connection-string-style" credential pattern we use for every other Azure
service. Managed identity is intentionally avoided so the same deploy
recipe works in ACA, CI pipelines, and on a developer laptop.
"""

from __future__ import annotations

import logging
import os
import threading
from typing import Any

from pydantic.fields import FieldInfo
from pydantic_settings.sources import PydanticBaseSettingsSource

logger = logging.getLogger(__name__)


# ── Secret allowlist ──────────────────────────────────────────────────────────
# Settings-class attribute names that may be materialized from Key Vault.
# Every secret-bearing field the application reads at runtime should be in
# this set; adding a new secret is a two-line change (here + the Settings
# class itself).
SECRET_FIELDS: frozenset[str] = frozenset(
    {
        # Infra
        "database_url",
        "redis_url",
        "jwt_secret_key",
        # Azure services (connection strings)
        "azure_servicebus_connection_string",
        "azure_storage_connection_string",
        "champion_storage_connection_string",
        # Function app keys (downstream services)
        "orchestrator_function_key",
        "de_inference_code",
        "customer_service_function_key",
        # External SaaS credentials
        "xds_username",
        "xds_password",
        "resend_api_key",
        "smtp_password",
        # First-boot bootstrap admin (rotated immediately after first login)
        "bootstrap_admin_password",
    }
)


def _field_name_to_secret_name(field_name: str) -> str:
    """``jwt_secret_key`` → ``jwt-secret-key``."""
    return field_name.replace("_", "-")


# ── Key Vault client bootstrap ────────────────────────────────────────────────


def _build_client():
    """Return an ``azure.keyvault.secrets.SecretClient`` or ``None``.

    Reads the vault URL + service principal from ``os.environ`` directly
    (not ``Settings``) to avoid a chicken-and-egg dependency when the
    Settings class is still being constructed.
    """
    vault_url = os.environ.get("AZURE_KEYVAULT_URL", "").strip()
    if not vault_url:
        return None

    tenant_id = os.environ.get("AZURE_TENANT_ID", "").strip()
    client_id = os.environ.get("AZURE_CLIENT_ID", "").strip()
    client_secret = os.environ.get("AZURE_CLIENT_SECRET", "").strip()

    if not (tenant_id and client_id and client_secret):
        logger.warning(
            "[keyvault] AZURE_KEYVAULT_URL is set but one of "
            "AZURE_TENANT_ID / AZURE_CLIENT_ID / AZURE_CLIENT_SECRET is "
            "missing — Key Vault source disabled, falling back to env vars.",
        )
        return None

    # Imported lazily so test suites don't pay the SDK import cost unless
    # a KV URL is actually configured.
    from azure.identity import ClientSecretCredential
    from azure.keyvault.secrets import SecretClient

    credential = ClientSecretCredential(
        tenant_id=tenant_id,
        client_id=client_id,
        client_secret=client_secret,
    )
    logger.info("[keyvault] Source enabled for vault %s", vault_url)
    return SecretClient(vault_url=vault_url, credential=credential)


# ── Source ────────────────────────────────────────────────────────────────────


class KeyVaultSettingsSource(PydanticBaseSettingsSource):
    """A pydantic-settings source backed by Azure Key Vault.

    Instances are cheap but the underlying ``SecretClient`` is shared
    across the process (built once, on first use) so repeated ``Settings``
    construction doesn't trigger new AAD token exchanges.
    """

    _client_lock = threading.Lock()
    _client = None  # class-level cache of SecretClient (or None when disabled)
    _client_built = False

    def __init__(self, settings_cls) -> None:
        super().__init__(settings_cls)
        self._secret_cache: dict[str, str] = {}

    # The base class exposes ``get_field_value`` as the per-field hook and
    # ``__call__`` as the bulk collector.  We implement both.

    @classmethod
    def _get_client(cls):
        if cls._client_built:
            return cls._client
        with cls._client_lock:
            if not cls._client_built:
                cls._client = _build_client()
                cls._client_built = True
        return cls._client

    @classmethod
    def reset(cls) -> None:
        """Clear the module-level client cache. Test hook only."""
        with cls._client_lock:
            cls._client = None
            cls._client_built = False

    def _fetch(self, field_name: str) -> str | None:
        if field_name in self._secret_cache:
            return self._secret_cache[field_name]

        client = self._get_client()
        if client is None:
            return None
        if field_name not in SECRET_FIELDS:
            return None

        secret_name = _field_name_to_secret_name(field_name)
        try:
            secret = client.get_secret(secret_name)
        except Exception as exc:  # ResourceNotFoundError or transient errors
            # Log at INFO for "not found" (expected for optional secrets),
            # WARNING for anything else.  Either way, fall through to env.
            name = type(exc).__name__
            level = logging.INFO if name == "ResourceNotFoundError" else logging.WARNING
            logger.log(
                level,
                "[keyvault] %s while fetching '%s' — falling back to env",
                name,
                secret_name,
            )
            return None

        value = secret.value or ""
        self._secret_cache[field_name] = value
        return value

    def get_field_value(self, field: FieldInfo, field_name: str) -> tuple[Any, str, bool]:
        value = self._fetch(field_name)
        # (value, field_key, value_is_complex)
        return value, field_name, False

    def __call__(self) -> dict[str, Any]:
        collected: dict[str, Any] = {}
        client = self._get_client()
        if client is None:
            return collected

        for field_name in self.settings_cls.model_fields:
            if field_name not in SECRET_FIELDS:
                continue
            value = self._fetch(field_name)
            if value:  # Only emit when KV actually has a value
                collected[field_name] = value
        return collected
