"""Model Version and Training Data Source models."""

from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import Date, DateTime, Enum, Float, ForeignKey, Integer, Numeric, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.user import AdminUser


class ModelStatus(str, enum.Enum):
    TRAINING = "training"
    VALIDATION = "validation"
    STAGING = "staging"
    PRODUCTION = "production"
    DEPRECATED = "deprecated"
    ARCHIVED = "archived"


class ModelVersion(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "model_versions"

    version: Mapped[str] = mapped_column(
        String(50), unique=True, nullable=False, index=True
    )
    model_type: Mapped[str] = mapped_column(String(50), nullable=False)
    status: Mapped[ModelStatus] = mapped_column(
        Enum(
            ModelStatus,
            name="model_status",
            values_callable=lambda x: [e.value for e in x],
        ),
        default=ModelStatus.TRAINING,
        nullable=False,
    )

    # Model performance metrics (AUC, KS, Gini, etc.)
    metrics: Mapped[dict | None] = mapped_column(JSONB)
    feature_count: Mapped[int | None] = mapped_column(Integer)
    training_data_ref: Mapped[str | None] = mapped_column(String(200))
    artifact_path: Mapped[str | None] = mapped_column(String(500))
    description: Mapped[str | None] = mapped_column(String(500))
    promoted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    promoted_by_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("admin_users.id")
    )

    # ── ML training pipeline fields ─────────────────────────────────────────
    training_upload_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("training_uploads.id"), index=True
    )
    data_source: Mapped[str | None] = mapped_column(String(200))
    mlflow_run_id: Mapped[str | None] = mapped_column(String(200))
    training_duration_seconds: Mapped[float | None] = mapped_column(Float)
    trained_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    training_status: Mapped[str | None] = mapped_column(String(50))

    # Rich JSON payloads from ML pipeline
    model_location: Mapped[dict | None] = mapped_column(JSONB)
    azure_ml_model: Mapped[dict | None] = mapped_column(JSONB)
    dataset_info: Mapped[dict | None] = mapped_column(JSONB)
    model_params: Mapped[dict | None] = mapped_column(JSONB)
    all_models_trained: Mapped[list | None] = mapped_column(JSONB)
    all_models_metrics: Mapped[dict | None] = mapped_column(JSONB)

    # Relationships
    promoted_by: Mapped["AdminUser | None"] = relationship()
    training_upload: Mapped["TrainingUpload | None"] = relationship(
        foreign_keys=[training_upload_id]
    )


class TrainingDataStatus(str, enum.Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    APPROVED = "approved"
    REJECTED = "rejected"
    FAILED = "failed"


class SourceType(str, enum.Enum):
    BANK = "bank"
    BUREAU = "bureau"
    TELCO = "telco"
    UTILITY = "utility"
    OTHER = "other"


class TrainingDataSource(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "training_data_sources"

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    source_type: Mapped[SourceType] = mapped_column(
        Enum(
            SourceType,
            name="source_type",
            values_callable=lambda x: [e.value for e in x],
        ),
        nullable=False,
    )
    provider: Mapped[str | None] = mapped_column(String(200))
    data_period_start: Mapped[datetime | None] = mapped_column(Date)
    data_period_end: Mapped[datetime | None] = mapped_column(Date)
    record_count: Mapped[int | None] = mapped_column(Integer)
    quality_score: Mapped[float | None] = mapped_column(Numeric(3, 2))
    transformation_status: Mapped[TrainingDataStatus] = mapped_column(
        Enum(
            TrainingDataStatus,
            name="training_data_status",
            values_callable=lambda x: [e.value for e in x],
        ),
        default=TrainingDataStatus.PENDING,
        nullable=False,
    )
    raw_file_path: Mapped[str | None] = mapped_column(String(500))
    transformed_path: Mapped[str | None] = mapped_column(String(500))
    quality_report: Mapped[dict | None] = mapped_column(JSONB)
    uploaded_by_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("admin_users.id")
    )

    # Relationships
    uploaded_by: Mapped["AdminUser | None"] = relationship()
