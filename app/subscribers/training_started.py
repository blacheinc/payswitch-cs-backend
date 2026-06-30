"""Subscriber handler for the model-training-started Service Bus topic.

When training begins, the ML pipeline publishes to this topic.
Sets the TrainingUpload status to TRAINING and stores pipeline metadata.

Updated for the new contract schema:
    {training_id, training_upload_id, status, record_count, models_to_train}
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

    Sets the TrainingUpload status to TRAINING and stores training metadata.

    Expected payload:
        training_id: str        - AI pipeline's training run ID
        training_upload_id: str - Backend's upload UUID
        status: str             - "STARTED"
        record_count: int       - Number of records used for training
        models_to_train: list   - Models being trained
    """
    upload_id = message.get("training_upload_id")
    training_id = message.get("training_id")

    if not upload_id:
        logger.error("[model-training-started] Missing training_upload_id")
        return

    logger.info(
        f"[model-training-started] Training started for upload={upload_id} "
        f"training_id={training_id}"
    )

    async with async_session_factory() as db:
        async with db.begin():
            result = await db.execute(select(TrainingUpload).where(TrainingUpload.id == upload_id))
            upload = result.scalar_one_or_none()
            if not upload:
                logger.warning(f"[model-training-started] TrainingUpload {upload_id} not found")
                return

            upload.status = UploadStatus.TRAINING

            # Store pipeline metadata from new contract fields
            if training_id:
                upload.training_id = training_id
            if message.get("record_count"):
                upload.record_count = message["record_count"]
            if message.get("models_to_train"):
                upload.models_to_train = message["models_to_train"]

    logger.info(
        f"[model-training-started] Upload {upload_id} -> TRAINING "
        f"(training_id={training_id}, "
        f"models={message.get('models_to_train', [])})"
    )
