"""Azure Blob Storage client for file uploads and downloads."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import BinaryIO, cast

from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


class BlobStorageClient:
    """Wraps Azure Blob Storage SDK for file operations."""

    def __init__(self) -> None:
        self._service_client = None

    def _get_service_client(self):
        if self._service_client is None:
            if not settings.azure_storage_connection_string:
                logger.warning("Azure Storage not configured.")
                return None
            from azure.storage.blob import BlobServiceClient

            self._service_client = BlobServiceClient.from_connection_string(
                settings.azure_storage_connection_string
            )
        return self._service_client

    def upload_file(
        self,
        container_name: str,
        blob_name: str,
        data: BinaryIO | bytes,
        content_type: str = "application/octet-stream",
    ) -> str | None:
        """Upload a file to Azure Blob Storage. Returns the blob URL."""
        client = self._get_service_client()
        if client is None:
            logger.debug("Skipping blob upload (not configured)")
            return None
        container_client = client.get_container_client(container_name)
        try:
            container_client.get_container_properties()
        except Exception:
            container_client.create_container()

        blob_client = container_client.get_blob_client(blob_name)
        blob_client.upload_blob(data, overwrite=True, content_type=content_type)
        return cast(str, blob_client.url)

    def generate_download_url(
        self,
        container_name: str,
        blob_name: str,
        expiry_hours: int = 1,
    ) -> str | None:
        """Generate a SAS URL for temporary download access."""
        client = self._get_service_client()
        if client is None:
            return None
        from azure.storage.blob import BlobSasPermissions, generate_blob_sas

        sas_token = generate_blob_sas(
            account_name=client.account_name,
            container_name=container_name,
            blob_name=blob_name,
            account_key=client.credential.account_key,
            permission=BlobSasPermissions(read=True),
            expiry=datetime.now(UTC) + timedelta(hours=expiry_hours),
        )
        blob_client = client.get_blob_client(container_name, blob_name)
        return f"{blob_client.url}?{sas_token}"

    def download_blob(self, container_name: str, blob_name: str) -> bytes | None:
        """Download a blob's content as bytes."""
        client = self._get_service_client()
        if client is None:
            return None
        blob_client = client.get_blob_client(container_name, blob_name)
        return cast(bytes, blob_client.download_blob().readall())

    def delete_blob(self, container_name: str, blob_name: str) -> None:
        """Delete a blob."""
        client = self._get_service_client()
        if client is None:
            return
        blob_client = client.get_blob_client(container_name, blob_name)
        blob_client.delete_blob()


# Singleton
_blob_client: BlobStorageClient | None = None


def get_blob_storage() -> BlobStorageClient:
    global _blob_client
    if _blob_client is None:
        _blob_client = BlobStorageClient()
    return _blob_client
