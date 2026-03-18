"""Subscriber handler for the model-training-started Service Bus topic.

When training begins, the ML pipeline publishes to this topic.
Sets the TrainingUpload status to TRAINING.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select

from app.db import async_session_factory
from app.models.data_source import TrainingUpload, UploadStatus

logger = logging.getLogger(__name__)


async def handle_model_training_started(message: dict[str, Any]) -> None:
    """Process a model-training-started message.

    Sets the TrainingUpload status to TRAINING.
    """
    upload_id = message.get("training_upload_id")

    if not upload_id:
        logger.error("[model-training-started] Missing training_upload_id")
        return

    logger.info(f"[model-training-started] Training started for upload {upload_id}")

    async with async_session_factory() as db:
        async with db.begin():
            result = await db.execute(
                select(TrainingUpload).where(TrainingUpload.id == upload_id)
            )
            upload = result.scalar_one_or_none()
            if not upload:
                logger.warning(
                    f"[model-training-started] TrainingUpload {upload_id} not found"
                )
                return

            upload.status = UploadStatus.TRAINING

    logger.info(f"[model-training-started] Upload {upload_id} -> training")
