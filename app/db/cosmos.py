"""Azure Cosmos DB client wrapper for score documents and audit logs."""

from __future__ import annotations

import logging
from typing import Any

from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


class CosmosClient:
    """Async wrapper for Azure Cosmos DB operations.

    Containers:
    - score_documents: Full score request/response pairs (partitioned by org_id)
    - audit_logs: Append-only audit trail (partitioned by org_id)
    """

    def __init__(self) -> None:
        self._client = None
        self._database = None
        self._containers: dict[str, Any] = {}

    async def _get_database(self):
        if self._database is None:
            if not settings.cosmos_endpoint or not settings.cosmos_key:
                logger.warning("Cosmos DB not configured. Operations will be no-ops.")
                return None
            from azure.cosmos.aio import CosmosClient as AzureCosmosClient

            self._client = AzureCosmosClient(
                settings.cosmos_endpoint,
                credential=settings.cosmos_key,
            )
            self._database = self._client.get_database_client(settings.cosmos_database)
        return self._database

    async def _get_container(self, container_name: str):
        if container_name not in self._containers:
            db = await self._get_database()
            if db is None:
                return None
            self._containers[container_name] = db.get_container_client(container_name)
        return self._containers[container_name]

    async def upsert_score_document(self, document: dict[str, Any]) -> None:
        """Store a full score request/response document."""
        container = await self._get_container("score_documents")
        if container is None:
            logger.debug("Skipping Cosmos upsert (not configured)")
            return
        await container.upsert_item(document)

    async def get_score_document(
        self, document_id: str, org_id: str
    ) -> dict[str, Any] | None:
        """Retrieve a score document by ID."""
        container = await self._get_container("score_documents")
        if container is None:
            return None
        try:
            return await container.read_item(item=document_id, partition_key=org_id)
        except Exception:
            return None

    async def write_audit_log(self, log_entry: dict[str, Any]) -> None:
        """Append an audit log entry."""
        container = await self._get_container("audit_logs")
        if container is None:
            logger.debug("Skipping Cosmos audit write (not configured)")
            return
        await container.upsert_item(log_entry)

    async def query_audit_logs(
        self, org_id: str, query_filter: str = "", max_items: int = 50
    ) -> list[dict[str, Any]]:
        """Query audit logs for an organization."""
        container = await self._get_container("audit_logs")
        if container is None:
            return []
        query = f"SELECT * FROM c WHERE c.organization_id = '{org_id}'"
        if query_filter:
            query += f" AND {query_filter}"
        query += " ORDER BY c.created_at DESC"

        items = []
        async for item in container.query_items(
            query=query,
            partition_key=org_id,
            max_item_count=max_items,
        ):
            items.append(item)
            if len(items) >= max_items:
                break
        return items

    async def close(self) -> None:
        if self._client:
            await self._client.close()
            self._client = None


# Singleton
_cosmos_client: CosmosClient | None = None


def get_cosmos_client() -> CosmosClient:
    global _cosmos_client
    if _cosmos_client is None:
        _cosmos_client = CosmosClient()
    return _cosmos_client
