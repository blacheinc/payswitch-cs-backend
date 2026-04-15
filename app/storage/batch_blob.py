"""Batch job blob reader — reads status and results from the champion storage account.

The orchestrator writes to model-artifacts/batch/{job_id}/:
  status.json   — live progress (pending/in_progress/complete)
  results.jsonl — one ScoringResponse per line, written on completion
"""

from __future__ import annotations

import json
import logging
from typing import Any, Generator

from app.storage.champion_blob import _get_connection_string

logger = logging.getLogger(__name__)

_CONTAINER = "model-artifacts"


def read_batch_status(job_id: str) -> dict[str, Any] | None:
    """Read model-artifacts/batch/{job_id}/status.json.

    Returns the parsed dict, or None if the blob doesn't exist yet
    (orchestrator hasn't started processing the job).
    """
    conn_str = _get_connection_string()
    if not conn_str:
        return None

    from azure.storage.blob import BlobServiceClient

    try:
        with BlobServiceClient.from_connection_string(conn_str) as svc:
            blob = (
                svc.get_container_client(_CONTAINER)
                   .get_blob_client(f"batch/{job_id}/status.json")
            )
            content = blob.download_blob().readall()
            return json.loads(content.decode("utf-8"))
    except Exception as e:
        name = type(e).__name__
        if "ResourceNotFound" in name or "BlobNotFound" in str(e):
            return None
        logger.warning("[batch-blob] Unexpected error reading status for %s: %s", job_id, e)
        raise


def stream_batch_results(blob_path: str) -> Generator[dict[str, Any], None, None]:
    """Stream one ScoringResponse dict per line from results.jsonl.

    blob_path is the full path within the container, e.g.
    "batch/BATCH-20260411-001/results.jsonl".

    Yields parsed dicts. Skips blank or unparseable lines.
    Raises on unexpected storage errors.
    """
    conn_str = _get_connection_string()
    if not conn_str:
        logger.warning("[batch-blob] Storage not configured — cannot stream results")
        return

    from azure.storage.blob import BlobServiceClient

    with BlobServiceClient.from_connection_string(conn_str) as svc:
        blob = svc.get_container_client(_CONTAINER).get_blob_client(blob_path)
        content = blob.download_blob().readall().decode("utf-8")

    for line in content.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            yield json.loads(line)
        except json.JSONDecodeError:
            logger.warning("[batch-blob] Skipping unparseable JSONL line: %s", line[:100])
