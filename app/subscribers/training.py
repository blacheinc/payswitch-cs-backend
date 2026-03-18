"""Subscriber handler for the model-training-completed Service Bus topic.

Processes messages from the ML pipeline when model training is complete.
Creates or updates a ModelVersion record and marks the TrainingUpload as COMPLETED.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

from app.db import async_session_factory
from app.db.base import utcnow
from app.models.data_source import TrainingUpload, UploadStatus
from app.models.model_version import ModelStatus, ModelVersion

logger = logging.getLogger(__name__)


async def handle_model_training_completed(message: dict[str, Any]) -> None:
    """Process a model-training-completed message.

    Creates a ModelVersion record with the full training results and
    transitions the linked TrainingUpload to COMPLETED status.

    Expected payload keys:
        training_upload_id, model_id, model_type, metrics,
        training_status, mlflow_run_id, data_source, trained_at,
        training_duration_seconds, model_location, azure_ml_model,
        dataset_info, model_params, all_models_trained, all_models_metrics
    """
    upload_id = message.get("training_upload_id")
    model_id = message.get("model_id")
    training_status = message.get("training_status", "unknown")

    if not upload_id:
        logger.error("[model-training-completed] Missing training_upload_id")
        return
    if not model_id:
        logger.error("[model-training-completed] Missing model_id")
        return

    logger.info(
        f"[model-training-completed] Processing model_id={model_id} "
        f"upload={upload_id} status={training_status}"
    )

    # Parse trained_at timestamp
    trained_at = None
    if message.get("trained_at"):
        try:
            trained_at = datetime.fromisoformat(message["trained_at"])
        except (ValueError, TypeError):
            logger.warning(
                f"[model-training-completed] Could not parse trained_at: {message['trained_at']}"
            )

    async with async_session_factory() as db:
        async with db.begin():
            # Check if this model version already exists (idempotency)
            existing = await db.execute(
                select(ModelVersion).where(ModelVersion.version == model_id)
            )
            model_version = existing.scalar_one_or_none()

            if model_version:
                logger.info(
                    f"[model-training-completed] Updating existing ModelVersion {model_id}"
                )
            else:
                model_version = ModelVersion(
                    version=model_id,
                    model_type=message.get("model_type", "unknown"),
                    status=ModelStatus.TRAINING,
                )
                db.add(model_version)

            # Populate all fields from the message
            model_version.metrics = message.get("metrics")
            model_version.training_upload_id = upload_id
            model_version.data_source = message.get("data_source")
            model_version.mlflow_run_id = message.get("mlflow_run_id")
            model_version.training_duration_seconds = message.get(
                "training_duration_seconds"
            )
            model_version.trained_at = trained_at
            model_version.training_status = training_status
            model_version.model_location = message.get("model_location")
            model_version.azure_ml_model = message.get("azure_ml_model")
            model_version.dataset_info = message.get("dataset_info")
            model_version.model_params = message.get("model_params")
            model_version.all_models_trained = message.get("all_models_trained")
            model_version.all_models_metrics = message.get("all_models_metrics")
            model_version.feature_count = (
                len(message["dataset_info"].get("features", []))
                if message.get("dataset_info")
                else None
            )

            # Set artifact path from model location
            if message.get("model_location"):
                model_version.artifact_path = message["model_location"].get(
                    "model_filename"
                )

            # Update status based on training result
            if training_status == "success":
                model_version.status = ModelStatus.VALIDATION
            else:
                model_version.status = ModelStatus.TRAINING

            # Update the TrainingUpload status
            result = await db.execute(
                select(TrainingUpload).where(TrainingUpload.id == upload_id)
            )
            upload = result.scalar_one_or_none()
            if upload:
                if training_status == "success":
                    upload.status = UploadStatus.TRAINED
                    upload.completed_at = utcnow()
                else:
                    upload.status = UploadStatus.INGESTING
                    upload.error_message = f"Model training failed: {training_status}"
            else:
                logger.warning(
                    f"[model-training-completed] TrainingUpload {upload_id} not found"
                )

    logger.info(
        f"[model-training-completed] ModelVersion {model_id} saved "
        f"(status={model_version.status.value}, "
        f"upload -> {'COMPLETED' if training_status == 'success' else 'FAILED'})"
    )
