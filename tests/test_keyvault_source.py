"""Tests for the Key Vault settings source.

The source must:
  * Be a no-op when ``AZURE_KEYVAULT_URL`` is unset.
  * Be a no-op when the vault URL is set but the SP creds are incomplete.
  * Return values from the vault for fields in ``SECRET_FIELDS``.
  * Fall through to env vars when a secret is missing in the vault.
  * Cache per-field lookups (no duplicate ``get_secret`` calls).
  * Skip lookups for fields not in the secret allowlist.

We never contact Azure in these tests — a fake ``SecretClient`` is
injected via monkeypatching the ``_build_client`` factory.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.config import Settings
from app.config import keyvault_source as kv_module
from app.config.keyvault_source import (
    SECRET_FIELDS,
    KeyVaultSettingsSource,
    _field_name_to_secret_name,
)

# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _clear_kv_state(monkeypatch):
    """Reset the module-level client cache + scrub KV-related env vars.

    Also evict the ``get_settings`` lru_cache so every test gets a fresh
    ``Settings`` instance (they ordinarily share one across the process).
    """
    KeyVaultSettingsSource.reset()
    for var in (
        "AZURE_KEYVAULT_URL",
        "AZURE_TENANT_ID",
        "AZURE_CLIENT_ID",
        "AZURE_CLIENT_SECRET",
    ):
        monkeypatch.delenv(var, raising=False)

    from app.config import get_settings

    get_settings.cache_clear()
    yield
    KeyVaultSettingsSource.reset()
    get_settings.cache_clear()


def _install_fake_client(
    monkeypatch,
    *,
    secrets: dict[str, str] | None = None,
    raise_on: set[str] | None = None,
    exc_type: type[Exception] | None = None,
):
    """Point ``_build_client`` at a fake ``SecretClient`` for this test.

    The fake returns string values from ``secrets`` keyed by the full
    kebab-case secret name; any name in ``raise_on`` triggers ``exc_type``.
    """
    calls: list[str] = []

    def _get_secret(name: str):
        calls.append(name)
        if raise_on and name in raise_on:
            raise (exc_type or RuntimeError)("boom")
        if secrets and name in secrets:
            return SimpleNamespace(value=secrets[name])
        # Mimic the real ResourceNotFoundError surface on unknown names.
        from azure.core.exceptions import ResourceNotFoundError

        raise ResourceNotFoundError(message=f"Secret '{name}' not found")

    client = MagicMock()
    client.get_secret.side_effect = _get_secret
    monkeypatch.setattr(kv_module, "_build_client", lambda: client)
    return client, calls


# ── Naming convention ────────────────────────────────────────────────────────


def test_field_name_to_secret_name_converts_underscores_to_hyphens():
    assert _field_name_to_secret_name("jwt_secret_key") == "jwt-secret-key"
    assert _field_name_to_secret_name("xds_username") == "xds-username"
    assert _field_name_to_secret_name("already-kebab") == "already-kebab"


# ── Disabled-mode behaviour ──────────────────────────────────────────────────


def test_source_noop_when_vault_url_unset(monkeypatch):
    """With no AZURE_KEYVAULT_URL the source must emit nothing."""
    # Set the env var for one of the secret fields so we can prove env wins.
    monkeypatch.setenv("JWT_SECRET_KEY", "from-env")

    settings = Settings()
    assert settings.jwt_secret_key == "from-env"
    # The source itself returns {} when disabled.
    src = KeyVaultSettingsSource(Settings)
    assert src() == {}


def test_source_noop_when_sp_creds_incomplete(monkeypatch, caplog):
    """Vault URL present but one of tenant/client/secret missing → disabled."""
    monkeypatch.setenv("AZURE_KEYVAULT_URL", "https://vault.example")
    monkeypatch.setenv("AZURE_TENANT_ID", "t")
    # AZURE_CLIENT_ID / AZURE_CLIENT_SECRET intentionally unset
    monkeypatch.setenv("JWT_SECRET_KEY", "from-env")

    with caplog.at_level("WARNING", logger="app.config.keyvault_source"):
        settings = Settings()

    assert settings.jwt_secret_key == "from-env"
    assert any("Key Vault source disabled" in m for m in caplog.messages)


# ── Enabled-mode behaviour ───────────────────────────────────────────────────


def _enable_sp_env(monkeypatch):
    monkeypatch.setenv("AZURE_KEYVAULT_URL", "https://vault.example")
    monkeypatch.setenv("AZURE_TENANT_ID", "t")
    monkeypatch.setenv("AZURE_CLIENT_ID", "c")
    monkeypatch.setenv("AZURE_CLIENT_SECRET", "s")


def test_kv_hit_overrides_env_for_secret_field(monkeypatch):
    _enable_sp_env(monkeypatch)
    _install_fake_client(
        monkeypatch,
        secrets={"jwt-secret-key": "from-kv"},
    )
    # Env provides a different value — KV must win (source is higher priority).
    monkeypatch.setenv("JWT_SECRET_KEY", "from-env")

    settings = Settings()
    assert settings.jwt_secret_key == "from-kv"


def test_kv_miss_falls_through_to_env(monkeypatch):
    """ResourceNotFoundError in KV must NOT shadow the env value."""
    _enable_sp_env(monkeypatch)
    _install_fake_client(monkeypatch, secrets={})  # empty vault → always misses
    monkeypatch.setenv("JWT_SECRET_KEY", "from-env")

    settings = Settings()
    assert settings.jwt_secret_key == "from-env"


def test_kv_transient_error_falls_through_to_env(monkeypatch, caplog):
    _enable_sp_env(monkeypatch)
    _install_fake_client(
        monkeypatch,
        raise_on={"jwt-secret-key"},
        exc_type=ConnectionError,
    )
    monkeypatch.setenv("JWT_SECRET_KEY", "from-env")

    with caplog.at_level("WARNING", logger="app.config.keyvault_source"):
        settings = Settings()

    assert settings.jwt_secret_key == "from-env"
    assert any("ConnectionError" in m for m in caplog.messages)


def test_non_secret_field_is_not_fetched_from_kv(monkeypatch):
    """URLs and other non-sensitive fields must not hit the vault."""
    _enable_sp_env(monkeypatch)
    _, calls = _install_fake_client(
        monkeypatch,
        secrets={"orchestrator-base-url": "https://attacker.example"},
    )
    monkeypatch.setenv("ORCHESTRATOR_BASE_URL", "https://legit.example")

    settings = Settings()
    assert settings.orchestrator_base_url == "https://legit.example"
    assert "orchestrator-base-url" not in calls


def test_cache_prevents_duplicate_fetches(monkeypatch):
    _enable_sp_env(monkeypatch)
    client, calls = _install_fake_client(
        monkeypatch,
        secrets={"jwt-secret-key": "from-kv"},
    )

    source = KeyVaultSettingsSource(Settings)
    # First call materializes the secret.
    first = source()
    assert first["jwt_secret_key"] == "from-kv"
    # Second call on the same instance must not re-fetch the same secret.
    first_count = calls.count("jwt-secret-key")
    _ = source()
    assert calls.count("jwt-secret-key") == first_count


def test_all_secret_fields_are_valid_settings_attributes():
    """Every entry in the allowlist must correspond to a real field."""
    known = set(Settings.model_fields.keys())
    unknown = SECRET_FIELDS - known
    assert not unknown, f"SECRET_FIELDS references fields not on Settings: {sorted(unknown)}"


def test_full_settings_build_uses_mix_of_kv_and_env(monkeypatch):
    """End-to-end: KV supplies some secrets, env supplies the rest."""
    _enable_sp_env(monkeypatch)
    _install_fake_client(
        monkeypatch,
        secrets={
            "jwt-secret-key": "kv-jwt",
            "xds-password": "kv-xds-password",
        },
    )
    monkeypatch.setenv("XDS_USERNAME", "env-xds-user")
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://env/db")

    s = Settings()
    assert s.jwt_secret_key == "kv-jwt"  # from KV
    assert s.xds_password == "kv-xds-password"  # from KV
    assert s.xds_username == "env-xds-user"  # env fallback (not in vault)
    assert s.database_url.endswith("/db")  # env fallback (not in vault)
