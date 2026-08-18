"""Subscriber handler for the data-ingested Service Bus topic.

Processes messages from the data team at each stage of the ingestion pipeline.
Dispatches on the `status` field in the payload and updates the TrainingUpload
record accordingly, applying any additional fields present in the message.

Status mapping (incoming → UploadStatus):
    TRANSFORMING → INGESTED   (ingestion done, data team starting transform)
    QUALIFIED    → TRANSFORMING (quality report received)
    TRANSFORMED  → TRANSFORMED (transformation complete)
    ERROR        → ERROR       (pipeline error)
"""

from __future__ import annotations

import json
import logging
from typing import Any
from uuid import UUID

from sqlalchemy import select

from app.db import async_session_factory
from app.db.base import utcnow
from app.models.data_source import TrainingUpload, UploadStatus

logger = logging.getLogger(__name__)

# Incoming status from data team → our UploadStatus
_STATUS_MAP: dict[str, UploadStatus] = {
    "TRANSFORMING": UploadStatus.INGESTED,
    "QUALIFIED": UploadStatus.TRANSFORMING,
    "TRANSFORMED": UploadStatus.TRANSFORMED,
    "ERROR": UploadStatus.ERROR,
}


async def handle_data_ingested(message: dict[str, Any]) -> None:
    """Process a data-ingested message and update the TrainingUpload record."""
    upload_id = message.get("training_upload_id")
    status_value = message.get("status")

    if not upload_id:
        logger.error("[data-ingested] Missing training_upload_id in message")
        return
    if not status_value:
        logger.error(f"[data-ingested] Missing 'status' in message for upload {upload_id}")
        return

    new_status = _STATUS_MAP.get(status_value)
    if not new_status:
        logger.error(f"[data-ingested] Unknown status '{status_value}' for upload {upload_id}")
        return

    logger.info(
        f"[data-ingested] Processing upload {upload_id}: {status_value} -> {new_status.value}"
    )

    async with async_session_factory() as db:
        async with db.begin():
            result = await db.execute(select(TrainingUpload).where(TrainingUpload.id == upload_id))
            upload = result.scalar_one_or_none()
            if not upload:
                logger.error(f"[data-ingested] Upload {upload_id} not found")
                return

            # Set the new status
            upload.status = new_status

            # Apply any additional fields present in the message
            if "quality_report" in message:
                upload.quality_report = message["quality_report"]
            if "quality_score" in message:
                upload.quality_score = message["quality_score"]
            if "transformed_file_path" in message:
                upload.transformed_file_path = message["transformed_file_path"]
            if "features_mapped" in message:
                upload.features_mapped = message["features_mapped"]
            if "error_report" in message:
                error_report = message["error_report"]
                if isinstance(error_report, dict):
                    upload.error_message = str(
                        error_report.get("user_message")
                        or error_report.get("technical_summary")
                        or json.dumps(error_report, default=str)
                    )
                elif isinstance(error_report, (list, tuple)):
                    upload.error_message = json.dumps(error_report, default=str)
                else:
                    upload.error_message = str(error_report)

            if "schema_template_id" in message and message["schema_template_id"]:
                try:
                    upload.mapping_template_id = UUID(str(message["schema_template_id"]))
                except ValueError:
                    # The data pipeline may send a logical template name such
                    # as ``deterministic-xds-v1``; the backend column stores
                    # only UUID-backed MappingTemplate records.
                    logger.info(
                        "[data-ingested] Ignoring non-UUID schema_template_id %r",
                        message["schema_template_id"],
                    )

            # Mark completion time on terminal statuses
            if new_status == UploadStatus.TRANSFORMED:
                upload.completed_at = utcnow()

    logger.info(f"[data-ingested] Upload {upload_id} -> {new_status.value}")
