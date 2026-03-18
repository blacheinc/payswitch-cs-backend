"""Data Source, Training Upload, Field Mapping, and Mapping Template models.

Supports the full ingestion pipeline:
  Upload → Parse → Map (DQA) → Review → Approve → Transform → Store
"""

from __future__ import annotations

import enum
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.user import AdminUser


# ── Enums ────────────────────────────────────────────────────────────────────


class SourceType(str, enum.Enum):
    BANK = "bank"
    BUREAU = "bureau"
    TELCO = "telco"
    UTILITY = "utility"
    MFI = "mfi"
    OTHER = "other"


class UploadStatus(str, enum.Enum):
    INGESTING = "ingesting"
    INGESTED = "ingested"
    TRANSFORMING = "transforming"
    TRANSFORMED = "transformed"
    TRAINING = "training"
    TRAINED = "trained"
    ERROR = "error"


class FileFormat(str, enum.Enum):
    CSV = "csv"
    JSON = "json"
    XLSX = "xlsx"
    TSV = "tsv"
    TXT = "txt"


class ConfidenceLevel(str, enum.Enum):
    GREEN = "green"  # > 0.90
    YELLOW = "yellow"  # 0.60 – 0.90
    RED = "red"  # < 0.60


class MappingSource(str, enum.Enum):
    TEMPLATE = "template"
    LLM = "llm"
    MANUAL = "manual"


# ── Data Source ──────────────────────────────────────────────────────────────


class DataSource(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Registry of banks / institutions that provide training data."""

    __tablename__ = "data_sources"

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    short_code: Mapped[str] = mapped_column(
        String(50), unique=True, nullable=False, index=True
    )
    source_type: Mapped[SourceType] = mapped_column(
        Enum(
            SourceType,
            name="data_source_type",
            values_callable=lambda x: [e.value for e in x],
        ),
        nullable=False,
    )
    description: Mapped[str | None] = mapped_column(Text)
    total_uploads: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    last_upload_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Relationships
    uploads: Mapped[list["TrainingUpload"]] = relationship(
        back_populates="data_source", lazy="selectin"
    )
    templates: Mapped[list["MappingTemplate"]] = relationship(
        back_populates="data_source", lazy="selectin"
    )


# ── Training Upload ─────────────────────────────────────────────────────────


class TrainingUpload(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One file upload through the ingestion pipeline."""

    __tablename__ = "training_uploads"

    data_source_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True), ForeignKey("data_sources.id"), nullable=False, index=True
    )
    status: Mapped[UploadStatus] = mapped_column(
        Enum(
            UploadStatus,
            name="upload_status",
            values_callable=lambda e: [m.value for m in e],
        ),
        default=UploadStatus.INGESTING,
        nullable=False,
        index=True,
    )

    # File info
    file_name: Mapped[str] = mapped_column(String(200), nullable=False)
    file_format: Mapped[FileFormat] = mapped_column(
        Enum(
            FileFormat,
            name="file_format",
            values_callable=lambda x: [e.value for e in x],
        ),
        nullable=False,
    )
    file_size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    raw_file_path: Mapped[str | None] = mapped_column(String(500))
    transformed_file_path: Mapped[str | None] = mapped_column(String(500))

    # Parsed metadata: columns, sample_rows, row_count
    file_metadata: Mapped[dict | None] = mapped_column(JSONB)
    record_count: Mapped[int | None] = mapped_column(Integer)
    valid_record_count: Mapped[int | None] = mapped_column(Integer)

    # Mapping stats
    features_mapped: Mapped[int | None] = mapped_column(Integer)
    target_features_total: Mapped[int] = mapped_column(
        Integer, default=18, server_default="18"
    )

    # Quality
    quality_report: Mapped[dict | None] = mapped_column(JSONB)
    quality_score: Mapped[float | None] = mapped_column(Numeric(3, 2))

    # Template used (if any)
    mapping_template_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("mapping_templates.id")
    )

    # Admin actions
    rejection_reason: Mapped[str | None] = mapped_column(Text)
    error_message: Mapped[str | None] = mapped_column(Text)
    uploaded_by_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("admin_users.id")
    )
    approved_by_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("admin_users.id")
    )
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Relationships
    data_source: Mapped["DataSource"] = relationship(back_populates="uploads")
    uploaded_by: Mapped["AdminUser | None"] = relationship(
        foreign_keys=[uploaded_by_id]
    )
    approved_by: Mapped["AdminUser | None"] = relationship(
        foreign_keys=[approved_by_id]
    )
    field_mappings: Mapped[list["FieldMapping"]] = relationship(
        back_populates="training_upload", cascade="all, delete-orphan", lazy="selectin"
    )
    mapping_template: Mapped["MappingTemplate | None"] = relationship(
        foreign_keys=[mapping_template_id]
    )


# ── Field Mapping ───────────────────────────────────────────────────────────


class FieldMapping(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One source_field → target_feature mapping for a given upload."""

    __tablename__ = "field_mappings"

    training_upload_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("training_uploads.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    source_field: Mapped[str] = mapped_column(String(200), nullable=False)
    target_feature: Mapped[str] = mapped_column(String(100), nullable=False)
    confidence: Mapped[float] = mapped_column(
        Numeric(5, 4), default=0.0, nullable=False
    )
    confidence_level: Mapped[ConfidenceLevel] = mapped_column(
        Enum(
            ConfidenceLevel,
            name="confidence_level",
            values_callable=lambda x: [e.value for e in x],
        ),
        default=ConfidenceLevel.RED,
        nullable=False,
    )
    mapping_source: Mapped[MappingSource] = mapped_column(
        Enum(
            MappingSource,
            name="mapping_source",
            values_callable=lambda x: [e.value for e in x],
        ),
        default=MappingSource.LLM,
        nullable=False,
    )
    sample_values: Mapped[list | None] = mapped_column(JSONB)
    data_type_detected: Mapped[str | None] = mapped_column(String(50))
    is_approved: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
    )
    admin_override_target: Mapped[str | None] = mapped_column(String(100))

    # Relationships
    training_upload: Mapped["TrainingUpload"] = relationship(
        back_populates="field_mappings"
    )


# ── Mapping Template ────────────────────────────────────────────────────────


class MappingTemplate(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Reusable field mapping template for a data source.

    When a bank uploads again, the saved template is loaded automatically.
    Admin still reviews every time, but mappings pre-populate.
    """

    __tablename__ = "mapping_templates"

    data_source_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True), ForeignKey("data_sources.id"), nullable=False, index=True
    )
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    mappings: Mapped[list[dict]] = mapped_column(JSONB, nullable=False)
    is_active: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="true", nullable=False
    )
    created_from_upload_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("training_uploads.id")
    )

    # Relationships
    data_source: Mapped["DataSource"] = relationship(back_populates="templates")
    created_from_upload: Mapped["TrainingUpload | None"] = relationship(
        foreign_keys=[created_from_upload_id]
    )
