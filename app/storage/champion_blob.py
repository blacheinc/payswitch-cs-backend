"""Champion model metadata blob reader.

Uses a connection string (same pattern as the rest of the app).
Prefers CHAMPION_STORAGE_CONNECTION_STRING; falls back to
AZURE_STORAGE_CONNECTION_STRING if that is not set separately.

Returns None if the blob doesn't exist yet (no training run completed).
"""

from __future__ import annotations

import json
import logging
from typing import Any, cast

from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

_CHAMPION_BLOB_PATH = "champions/current.json"


def _get_connection_string() -> str | None:
    """Return the connection string to use for the champion storage account.

    Uses CHAMPION_STORAGE_CONNECTION_STRING if set, otherwise falls back to
    AZURE_STORAGE_CONNECTION_STRING. Returns None if neither is configured.
    """
    return (
        settings.champion_storage_connection_string
        or settings.azure_storage_connection_string
        or None
    )


def read_champion_metadata() -> dict[str, Any] | None:
    """Read champions/current.json from the model-artifacts container.

    Returns parsed JSON dict, or None if the blob doesn't exist yet or
    if storage is not configured.
    Raises on unexpected errors so the caller can return 500.
    """
    conn_str = _get_connection_string()
    if not conn_str:
        logger.info("Champion storage not configured — blob unavailable")
        return None

    from azure.storage.blob import BlobServiceClient

    try:
        with BlobServiceClient.from_connection_string(conn_str) as svc:
            blob_client = svc.get_container_client(
                settings.champion_storage_container
            ).get_blob_client(_CHAMPION_BLOB_PATH)
            content = blob_client.download_blob().readall()
            return cast(dict[str, Any], json.loads(content.decode("utf-8")))

    except Exception as e:
        name = type(e).__name__
        if "ResourceNotFound" in name or "BlobNotFound" in str(e) or "ContainerNotFound" in str(e):
            logger.info("Champion blob not found (no completed training run yet)")
            return None
        raise
