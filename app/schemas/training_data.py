"""Pydantic schemas for the training data ingestion pipeline."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

# ── Data Source Schemas ──────────────────────────────────────────────────────


class CreateDataSourceRequest(BaseModel):
    name: str = Field(..., max_length=200, description="Institution name, e.g. 'GCB Bank'")
    short_code: str = Field(
        ...,
        max_length=50,
        pattern=r"^[a-z0-9][a-z0-9\-]*$",
        description="URL-safe slug, e.g. 'gcb-bank'",
    )
    source_type: str = Field(..., description="bank | bureau | telco | utility | mfi | other")
    description: str | None = None


class DataSourceResponse(BaseModel):
    id: UUID
    name: str
    short_code: str
    source_type: str
    description: str | None = None
    total_uploads: int = 0
    last_upload_at: datetime | None = None
    created_at: datetime
    updated_at: datetime | None = None

    model_config = {"from_attributes": True}


class DataSourceDetailResponse(DataSourceResponse):
    """Includes upload count and template availability."""

    has_active_template: bool = False
    active_template_id: UUID | None = None


# ── Mapping Template Schemas ─────────────────────────────────────────────────


class MappingTemplateResponse(BaseModel):
    id: UUID
    data_source_id: UUID
    version: int
    mappings: list[dict[str, Any]]
    is_active: bool
    created_from_upload_id: UUID | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


# ── Field Mapping Schemas ────────────────────────────────────────────────────


class FieldMappingItem(BaseModel):
    id: UUID | None = None
    source_field: str
    target_feature: str
    confidence: float = 0.0
    confidence_level: str = "red"
    mapping_source: str = "llm"
    sample_values: list[Any] | None = None
    data_type_detected: str | None = None
    is_approved: bool = False
    admin_override_target: str | None = None

    model_config = {"from_attributes": True}


class UpdateFieldMappingItem(BaseModel):
    """Admin correction for a single mapping."""

    source_field: str
    target_feature: str
    is_approved: bool = True


class UpdateMappingsRequest(BaseModel):
    """Batch update for field mappings."""

    mappings: list[UpdateFieldMappingItem]


class MappingResultResponse(BaseModel):
    """Full mapping result for admin review."""

    upload_id: UUID
    status: str
    features_mapped: int
    target_features_total: int = 18
    has_target_variable: bool = False
    quality_warnings: list[str] = []
    mappings: list[FieldMappingItem]
    sample_data: list[dict[str, Any]] | None = None


# ── Training Upload Schemas ──────────────────────────────────────────────────


class TrainingUploadResponse(BaseModel):
    id: UUID
    data_source_id: UUID
    data_source_name: str | None = None
    status: str
    file_name: str
    file_format: str
    file_size_bytes: int | None = None
    record_count: int | None = None
    valid_record_count: int | None = None
    features_mapped: int | None = None
    target_features_total: int = 18
    quality_score: float | None = None
    quality_report: dict | None = None
    rejection_reason: str | None = None
    error_message: str | None = None
    uploaded_by_id: UUID | None = None
    approved_by_id: UUID | None = None
    approved_at: datetime | None = None
    completed_at: datetime | None = None
    created_at: datetime
    updated_at: datetime | None = None

    model_config = {"from_attributes": True}


class UploadStatusResponse(BaseModel):
    """Lightweight status poll response."""

    upload_id: UUID
    status: str
    features_mapped: int | None = None
    target_features_total: int = 18
    error_message: str | None = None


class ApproveUploadRequest(BaseModel):
    """Optional body for approval (can be empty)."""

    notes: str | None = None


class RejectUploadRequest(BaseModel):
    reason: str = Field(..., min_length=1, max_length=1000)


class ApproveUploadResponse(BaseModel):
    upload_id: UUID
    status: str
    transformed_file_path: str | None = None
    warnings: list[str] = []


class MappingCallbackPayload(BaseModel):
    """Payload the Data Quality Agent sends back after mapping."""

    upload_id: UUID
    mappings: list[FieldMappingItem]
    quality_report: dict[str, Any] | None = None
    features_mapped: int
    features_total: int = 18


class IngestionCallbackPayload(BaseModel):
    """Payload received from the data team after ingestion is complete."""

    training_upload_id: UUID
    transformed_file_path: str
    features_mapped: int
    quality_report: dict[str, Any] | None = None
    quality_score: float | None = None
    schema_template_id: UUID | None = None
