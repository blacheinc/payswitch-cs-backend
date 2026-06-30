"""Subscriber handler for the model-training-completed Service Bus topic.

Processes messages from the ML pipeline when model training is complete.
Creates a ModelVersion record for EACH model in models_trained and
marks the TrainingUpload as COMPLETED or FAILED.

Updated for the new contract — per-model results instead of monolithic:
    models_trained: {
        credit_risk: {status, model_version, metrics},
        fraud_detection: {status, model_version, metrics},
        loan_amount: {status, model_version, metrics},
        income_verification: {status, model_version, metrics}
    }
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select

from app.db import async_session_factory
from app.db.base import utcnow
from app.models.data_source import TrainingUpload, UploadStatus
from app.models.model_metric import ModelMetric
from app.models.model_version import ModelStatus, ModelVersion

logger = logging.getLogger(__name__)


async def handle_model_training_completed(message: dict[str, Any]) -> None:
    """Process a model-training-completed message.

    Creates/updates ModelVersion records for each model and
    transitions the linked TrainingUpload to TRAINED or ERROR.

    Expected success payload:
        training_id, training_upload_id, status: "COMPLETE",
        all_succeeded: true/false, training_duration_seconds,
        models_trained: { model_type: {status, model_version, metrics} }

    Expected failure payload:
        training_id, training_upload_id, status: "FAILED",
        all_succeeded: false, dataset_info: {errors: [...]}
    """
    upload_id = message.get("training_upload_id")
    training_id = message.get("training_id")
    training_status = message.get("status", "unknown").upper()
    all_succeeded = message.get("all_succeeded", False)

    if not upload_id:
        logger.error("[model-training-completed] Missing training_upload_id")
        return

    logger.info(
        f"[model-training-completed] Processing training_id={training_id} "
        f"upload={upload_id} status={training_status} all_succeeded={all_succeeded}"
    )

    async with async_session_factory() as db:
        async with db.begin():
            # Process per-model results
            models_trained = message.get("models_trained", {})
            created_versions = []

            for model_type, model_data in models_trained.items():
                if not isinstance(model_data, dict):
                    continue

                model_version_str = model_data.get("model_version", "")
                model_status_str = model_data.get("status", "unknown")
                model_metrics = model_data.get("metrics", {})

                # Check for existing version (idempotency) — keyed on (model_type, version)
                existing = await db.execute(
                    select(ModelVersion).where(
                        ModelVersion.model_type == model_type,
                        ModelVersion.version == model_version_str,
                    )
                )
                model_version = existing.scalar_one_or_none()

                if model_version:
                    logger.info(
                        f"[model-training-completed] Updating existing "
                        f"ModelVersion {model_version_str}"
                    )
                else:
                    model_version = ModelVersion(
                        version=model_version_str,
                        model_type=model_type,
                        status=ModelStatus.TRAINING,
                    )
                    db.add(model_version)

                # Populate fields
                model_version.training_upload_id = upload_id
                model_version.training_id = training_id
                model_version.metrics = model_metrics
                model_version.training_status = model_status_str
                model_version.training_duration_seconds = message.get("training_duration_seconds")
                model_version.trained_at = utcnow()
                model_version.dataset_info = message.get("dataset_info")

                # Update status per model
                if model_status_str.lower() in ("success", "complete"):
                    model_version.status = ModelStatus.VALIDATION
                else:
                    model_version.status = ModelStatus.TRAINING

                created_versions.append(model_version_str)

                # Write training history record immediately (feeds model-ops retraining table)
                if model_status_str.lower() in ("success", "complete"):
                    db.add(
                        ModelMetric(
                            model_type=model_type,
                            model_version=model_version_str,
                            training_id=training_id,
                            metric_type="training",
                            metrics=model_metrics,
                            timestamp=utcnow(),
                        )
                    )

            # Update TrainingUpload
            result = await db.execute(select(TrainingUpload).where(TrainingUpload.id == upload_id))
            upload = result.scalar_one_or_none()
            if upload:
                if training_id:
                    upload.training_id = training_id

                if training_status == "COMPLETE" and all_succeeded:
                    upload.status = UploadStatus.TRAINED
                    upload.completed_at = utcnow()
                elif training_status == "FAILED":
                    upload.status = UploadStatus.ERROR
                    # Store error details
                    dataset_info = message.get("dataset_info", {})
                    errors = dataset_info.get("errors", [])
                    upload.error_message = (
                        f"Training failed: {', '.join(errors)}"
                        if errors
                        else f"Training failed: {training_status}"
                    )
                else:
                    # Partial success — some models succeeded, some didn't
                    upload.status = UploadStatus.TRAINED
                    upload.completed_at = utcnow()
                    upload.error_message = "Partial training success — not all models succeeded"
            else:
                logger.warning(f"[model-training-completed] TrainingUpload {upload_id} not found")

    logger.info(
        f"[model-training-completed] Saved {len(created_versions)} ModelVersions: "
        f"{created_versions} (upload={upload_id} -> "
        f"{'TRAINED' if all_succeeded else 'ERROR/PARTIAL'})"
    )
