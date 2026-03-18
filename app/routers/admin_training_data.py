"""Training Data upload, mapping, approve/reject/retry router."""

from __future__ import annotations

import logging
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import get_settings
from app.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.core.pagination import PaginatedResponse
from app.db import get_db
from app.db.base import utcnow
from app.dependencies import get_current_admin
from app.models.data_source import (
    ConfidenceLevel,
    DataSource,
    FieldMapping,
    FileFormat,
    MappingSource,
    MappingTemplate,
    TrainingUpload,
    UploadStatus,
)
from app.models.user import AdminUser
from app.schemas.training_data import (
    ApproveUploadRequest,
    ApproveUploadResponse,
    FieldMappingItem,
    MappingCallbackPayload,
    MappingResultResponse,
    RejectUploadRequest,
    TrainingUploadResponse,
    UpdateMappingsRequest,
    UploadStatusResponse,
)
from app.services.file_parser import parse_file
from app.services.training_data_features import (
    MIN_FEATURES_MAPPED,
    MIN_RECORD_COUNT,
    TARGET_VARIABLE,
)

logger = logging.getLogger(__name__)
settings = get_settings()

router = APIRouter(prefix="/admin/training-data", tags=["Admin – Training Data"])


def _build_upload_response(upload: TrainingUpload) -> TrainingUploadResponse:
    """Build a TrainingUploadResponse with data_source_name populated."""
    resp = TrainingUploadResponse.model_validate(upload)
    if upload.data_source:
        resp.data_source_name = upload.data_source.name
    return resp


# ── Upload ───────────────────────────────────────────────────────────────────


@router.post(
    "/upload",
    response_model=TrainingUploadResponse,
    status_code=status.HTTP_201_CREATED,
)
async def upload_training_data(
    file: Annotated[UploadFile, File(description="Training data file")],
    source_id: Annotated[str, Form(description="Data source UUID")],
    admin: Annotated[AdminUser, Depends(get_current_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> TrainingUploadResponse:
    """Upload a bank data file for mapping and training.

    Accepts CSV, JSON, XLSX, TSV, or TXT files.
    The file is parsed synchronously to extract headers and sample rows,
    then a Service Bus event is published to trigger the Data Quality Agent.
    """
    # Validate source exists
    result = await db.execute(select(DataSource).where(DataSource.id == source_id))
    source = result.scalar_one_or_none()
    if not source:
        raise NotFoundError("Data source", source_id)

    # Validate file format
    if not file.filename:
        raise ValidationError("File must have a filename")

    ext = file.filename.rsplit(".", 1)[-1].lower()
    try:
        file_format = FileFormat(ext)
    except ValueError:
        raise ValidationError(
            f"Unsupported file format '.{ext}'. Accepted: csv, json, xlsx, tsv, txt"
        )

    # Read file content
    file_bytes = await file.read()
    if len(file_bytes) == 0:
        raise ValidationError("File is empty")

    # Create upload record
    upload_id = uuid.uuid4()
    raw_path = f"{source.id}/{file.filename}"

    upload = TrainingUpload(
        id=upload_id,
        data_source_id=source.id,
        status=UploadStatus.INGESTING,
        file_name=file.filename,
        file_format=file_format,
        file_size_bytes=len(file_bytes),
        raw_file_path=raw_path,
        uploaded_by_id=admin.id,
    )
    db.add(upload)
    await db.flush()

    # Store raw file in Blob Storage
    try:
        from app.storage.blob import get_blob_storage

        blob = get_blob_storage()
        blob.upload_file("data", raw_path, file_bytes)
    except Exception as e:
        logger.warning(f"Blob upload failed (non-fatal for local dev): {e}")

    # Parse file to extract headers + sample
    try:
        parse_result = parse_file(file_bytes, file.filename)
        upload.file_metadata = parse_result.to_dict()
        upload.record_count = parse_result.row_count
        upload.status = UploadStatus.INGESTING
    except ValueError as e:
        upload.status = UploadStatus.INGESTING
        upload.error_message = str(e)
        await db.flush()
        raise ValidationError(str(e))

    # Check for existing mapping template
    template_result = await db.execute(
        select(MappingTemplate).where(
            MappingTemplate.data_source_id == source.id,
            MappingTemplate.is_active == True,  # noqa: E712
        )
    )
    active_template = template_result.scalar_one_or_none()

    # Publish Service Bus event for Data Quality Agent
    try:
        from app.core.events import get_event_publisher

        publisher = get_event_publisher()
        await publisher.publish(
            topic_name="data-awaits-ingestion",
            message={
                "training_upload_id": str(upload.id),
                "data_source_id": str(source.id),
                "file_format": ext,
                "file_size_bytes": len(file_bytes),
                "raw_file_path": raw_path,
            },
        )
        upload.status = UploadStatus.INGESTING
    except Exception as e:
        logger.warning(f"Service Bus publish failed: {e}")
        # Don't fail the upload — DQA can be triggered manually or via retry

    # Update source stats
    source.total_uploads = (source.total_uploads or 0) + 1
    source.last_upload_at = utcnow()

    await db.flush()
    await db.refresh(upload)

    resp = TrainingUploadResponse.model_validate(upload)
    resp.data_source_name = source.name
    return resp


# ── List / Get Uploads ───────────────────────────────────────────────────────


@router.get("", response_model=PaginatedResponse[TrainingUploadResponse])
async def list_uploads(
    admin: Annotated[AdminUser, Depends(get_current_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    status_filter: str | None = Query(None, alias="status"),
    source_id: str | None = Query(None),
) -> PaginatedResponse[TrainingUploadResponse]:
    """List all training data uploads (paginated)."""
    query = select(TrainingUpload).options(selectinload(TrainingUpload.data_source))
    count_query = select(func.count(TrainingUpload.id))

    if status_filter:
        query = query.where(TrainingUpload.status == UploadStatus(status_filter))
        count_query = count_query.where(
            TrainingUpload.status == UploadStatus(status_filter)
        )
    if source_id:
        query = query.where(TrainingUpload.data_source_id == source_id)
        count_query = count_query.where(TrainingUpload.data_source_id == source_id)

    total = (await db.execute(count_query)).scalar_one()
    result = await db.execute(
        query.order_by(TrainingUpload.created_at.desc())
        .offset((page - 1) * per_page)
        .limit(per_page)
    )
    uploads = result.scalars().all()

    return PaginatedResponse.create(
        items=[_build_upload_response(u) for u in uploads],
        total=total,
        page=page,
        per_page=per_page,
    )


@router.get("/{upload_id}", response_model=TrainingUploadResponse)
async def get_upload(
    upload_id: str,
    admin: Annotated[AdminUser, Depends(get_current_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> TrainingUploadResponse:
    """Get upload details + quality report."""
    result = await db.execute(
        select(TrainingUpload)
        .options(selectinload(TrainingUpload.data_source))
        .where(TrainingUpload.id == upload_id)
    )
    upload = result.scalar_one_or_none()
    if not upload:
        raise NotFoundError("Training upload", upload_id)
    await db.refresh(upload)
    return _build_upload_response(upload)


@router.get("/{upload_id}/status", response_model=UploadStatusResponse)
async def get_upload_status(
    upload_id: str,
    admin: Annotated[AdminUser, Depends(get_current_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> UploadStatusResponse:
    """Lightweight status poll for frontend."""
    result = await db.execute(
        select(TrainingUpload).where(TrainingUpload.id == upload_id)
    )
    upload = result.scalar_one_or_none()
    if not upload:
        raise NotFoundError("Training upload", upload_id)

    return UploadStatusResponse(
        upload_id=upload.id,
        status=upload.status.value,
        features_mapped=upload.features_mapped,
        target_features_total=upload.target_features_total,
        error_message=upload.error_message,
    )


# ── Mapping Review ───────────────────────────────────────────────────────────


@router.get("/{upload_id}/mapping", response_model=MappingResultResponse)
async def get_mapping(
    upload_id: str,
    admin: Annotated[AdminUser, Depends(get_current_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> MappingResultResponse:
    """Get field mappings + sample data for admin review."""
    result = await db.execute(
        select(TrainingUpload).where(TrainingUpload.id == upload_id)
    )
    upload = result.scalar_one_or_none()
    if not upload:
        raise NotFoundError("Training upload", upload_id)

    # Get field mappings
    mapping_result = await db.execute(
        select(FieldMapping).where(FieldMapping.training_upload_id == upload.id)
    )
    mappings = mapping_result.scalars().all()

    # Generate warnings
    warnings: list[str] = []
    mapped_targets = [
        m.target_feature for m in mappings if m.target_feature != "_unmapped"
    ]
    features_mapped = len(set(mapped_targets))

    has_target = TARGET_VARIABLE in mapped_targets
    if not has_target:
        warnings.append(
            f"Target variable '{TARGET_VARIABLE}' is not mapped. "
            "This is mandatory for training."
        )
    if features_mapped < MIN_FEATURES_MAPPED:
        warnings.append(
            f"Only {features_mapped} of 18 features mapped "
            f"(minimum: {MIN_FEATURES_MAPPED})."
        )
    if upload.record_count and upload.record_count < MIN_RECORD_COUNT:
        warnings.append(
            f"Only {upload.record_count} records (minimum: {MIN_RECORD_COUNT})."
        )

    # Get sample data from file_metadata
    sample_data = None
    if upload.file_metadata:
        columns = upload.file_metadata.get("columns", [])
        sample_rows = upload.file_metadata.get("sample_rows", [])
        if columns and sample_rows:
            sample_data = [dict(zip(columns, row)) for row in sample_rows[:5]]

    return MappingResultResponse(
        upload_id=upload.id,
        status=upload.status.value,
        features_mapped=features_mapped,
        target_features_total=18,
        has_target_variable=has_target,
        quality_warnings=warnings,
        mappings=[FieldMappingItem.model_validate(m) for m in mappings],
        sample_data=sample_data,
    )


@router.put("/{upload_id}/mapping", response_model=MappingResultResponse)
async def update_mapping(
    upload_id: str,
    body: UpdateMappingsRequest,
    admin: Annotated[AdminUser, Depends(get_current_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> MappingResultResponse:
    """Admin corrects field mappings before approval."""
    result = await db.execute(
        select(TrainingUpload).where(TrainingUpload.id == upload_id)
    )
    upload = result.scalar_one_or_none()
    if not upload:
        raise NotFoundError("Training upload", upload_id)

    if upload.status not in (UploadStatus.INGESTED, UploadStatus.INGESTED):
        raise ConflictError(
            f"Cannot edit mappings in status '{upload.status.value}'. "
            "Must be 'ingested'."
        )

    # Apply corrections
    for correction in body.mappings:
        mapping_result = await db.execute(
            select(FieldMapping).where(
                FieldMapping.training_upload_id == upload.id,
                FieldMapping.source_field == correction.source_field,
            )
        )
        mapping = mapping_result.scalar_one_or_none()
        if mapping:
            mapping.admin_override_target = correction.target_feature
            mapping.target_feature = correction.target_feature
            mapping.mapping_source = MappingSource.MANUAL
            mapping.is_approved = correction.is_approved
            mapping.confidence = 1.0
            mapping.confidence_level = ConfidenceLevel.GREEN
        else:
            # New manual mapping
            new_mapping = FieldMapping(
                training_upload_id=upload.id,
                source_field=correction.source_field,
                target_feature=correction.target_feature,
                confidence=1.0,
                confidence_level=ConfidenceLevel.GREEN,
                mapping_source=MappingSource.MANUAL,
                is_approved=correction.is_approved,
            )
            db.add(new_mapping)

    upload.status = UploadStatus.INGESTED
    await db.flush()

    # Re-fetch and return updated mapping
    return await get_mapping(upload_id, admin, db)


# ── Approve / Reject / Retry ────────────────────────────────────────────────


@router.post("/{upload_id}/approve", response_model=ApproveUploadResponse)
async def approve_upload(
    upload_id: str,
    admin: Annotated[AdminUser, Depends(get_current_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
    body: ApproveUploadRequest | None = None,
) -> ApproveUploadResponse:
    """Approve mapping and trigger data transformation."""
    result = await db.execute(
        select(TrainingUpload).where(TrainingUpload.id == upload_id)
    )
    upload = result.scalar_one_or_none()
    if not upload:
        raise NotFoundError("Training upload", upload_id)

    if upload.status not in (UploadStatus.INGESTED, UploadStatus.INGESTED):
        raise ConflictError(
            f"Cannot approve in status '{upload.status.value}'. " "Must be 'ingested'."
        )

    upload.status = UploadStatus.INGESTED
    upload.approved_by_id = admin.id
    upload.approved_at = utcnow()
    await db.flush()

    # Get approved mappings
    mapping_result = await db.execute(
        select(FieldMapping).where(FieldMapping.training_upload_id == upload.id)
    )
    mappings = mapping_result.scalars().all()

    # Build mapping list for transform
    approved_mappings = [
        {"source_field": m.source_field, "target_feature": m.target_feature}
        for m in mappings
        if m.target_feature != "_unmapped"
    ]

    # Transform the data
    upload.status = UploadStatus.TRANSFORMING
    await db.flush()

    warnings: list[str] = []
    transformed_path: str | None = None

    try:
        # Load raw file from Blob
        raw_bytes: bytes | None = None
        try:
            from app.storage.blob import get_blob_storage

            blob = get_blob_storage()
            raw_bytes = blob.download_blob("training-data", upload.raw_file_path or "")
        except Exception as e:
            logger.warning(f"Blob download failed: {e}")

        if raw_bytes is None:
            # Fallback: file not in blob (local dev without Azurite)
            upload.status = UploadStatus.TRAINED
            upload.completed_at = utcnow()
            warnings.append(
                "Transform skipped: raw file not accessible from blob storage. "
                "Pipeline completed with mapping only."
            )
        else:
            from app.services.transform_service import transform_data

            transform_result = transform_data(
                raw_file_bytes=raw_bytes,
                file_name=upload.file_name,
                mappings=approved_mappings,
            )

            # Get source short_code for path
            source_result = await db.execute(
                select(DataSource).where(DataSource.id == upload.data_source_id)
            )
            source = source_result.scalar_one()

            # Store transformed file
            transformed_path = f"transformed/{source.short_code}/{upload.id}.csv"
            try:
                blob.upload_file(
                    "training-data", transformed_path, transform_result.csv_bytes
                )
            except Exception as e:
                logger.warning(f"Blob upload of transformed data failed: {e}")

            upload.transformed_file_path = transformed_path
            upload.valid_record_count = transform_result.valid_record_count
            upload.quality_report = transform_result.quality_report
            upload.quality_score = transform_result.quality_score
            upload.status = UploadStatus.TRAINED
            upload.completed_at = utcnow()
            warnings = transform_result.warnings

        # Save/update mapping template for this source
        await _save_mapping_template(db, upload, mappings)

        # Publish quality.approved event
        try:
            from app.core.events import get_event_publisher

            publisher = get_event_publisher()
            await publisher.publish(
                topic_name="quality.approved",
                message={
                    "event_type": "quality.approved",
                    "upload_id": str(upload.id),
                    "data_source_id": str(upload.data_source_id),
                    "transformed_file_path": transformed_path,
                    "record_count": upload.valid_record_count or upload.record_count,
                    "features_mapped": upload.features_mapped,
                },
            )
        except Exception as e:
            logger.warning(f"quality.approved event publish failed: {e}")

    except Exception as e:
        logger.exception(f"Transform failed for upload {upload_id}")
        upload.status = UploadStatus.INGESTING
        upload.error_message = str(e)[:500]

    await db.flush()

    return ApproveUploadResponse(
        upload_id=upload.id,
        status=upload.status.value,
        transformed_file_path=transformed_path,
        warnings=warnings,
    )


@router.post("/{upload_id}/reject", response_model=TrainingUploadResponse)
async def reject_upload(
    upload_id: str,
    body: RejectUploadRequest,
    admin: Annotated[AdminUser, Depends(get_current_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> TrainingUploadResponse:
    """Reject a mapping with a reason."""
    result = await db.execute(
        select(TrainingUpload)
        .options(selectinload(TrainingUpload.data_source))
        .where(TrainingUpload.id == upload_id)
    )
    upload = result.scalar_one_or_none()
    if not upload:
        raise NotFoundError("Training upload", upload_id)

    upload.status = UploadStatus.INGESTED
    upload.rejection_reason = body.reason
    await db.flush()
    await db.refresh(upload, attribute_names=["data_source"])

    return _build_upload_response(upload)


@router.post("/{upload_id}/retry", response_model=UploadStatusResponse)
async def retry_upload(
    upload_id: str,
    admin: Annotated[AdminUser, Depends(get_current_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> UploadStatusResponse:
    """Re-trigger the Data Quality Agent for a failed upload."""
    result = await db.execute(
        select(TrainingUpload).where(TrainingUpload.id == upload_id)
    )
    upload = result.scalar_one_or_none()
    if not upload:
        raise NotFoundError("Training upload", upload_id)

    if upload.status not in (UploadStatus.INGESTING, UploadStatus.INGESTING):
        raise ConflictError(
            f"Can only retry uploads in 'ingesting' status, "
            f"current: '{upload.status.value}'."
        )

    upload.status = UploadStatus.INGESTING
    upload.error_message = None
    await db.flush()

    # Re-publish Service Bus event
    try:
        source_result = await db.execute(
            select(DataSource).where(DataSource.id == upload.data_source_id)
        )
        source = source_result.scalar_one()

        # Check for template
        template_result = await db.execute(
            select(MappingTemplate).where(
                MappingTemplate.data_source_id == source.id,
                MappingTemplate.is_active == True,  # noqa: E712
            )
        )
        active_template = template_result.scalar_one_or_none()

        from app.core.events import get_event_publisher

        publisher = get_event_publisher()
        await publisher.publish(
            topic_name="data.uploaded",
            message={
                "event_type": "data.uploaded",
                "upload_id": str(upload.id),
                "data_source_id": str(source.id),
                "source_short_code": source.short_code,
                "file_format": upload.file_format.value,
                "raw_file_path": upload.raw_file_path,
                "columns": (
                    upload.file_metadata.get("columns", [])
                    if upload.file_metadata
                    else []
                ),
                "sample_rows": (
                    upload.file_metadata.get("sample_rows", [])[:10]
                    if upload.file_metadata
                    else []
                ),
                "row_count": upload.record_count or 0,
                "existing_template_id": (
                    str(active_template.id) if active_template else None
                ),
            },
        )
    except Exception as e:
        logger.warning(f"Service Bus re-publish failed: {e}")

    return UploadStatusResponse(
        upload_id=upload.id,
        status=upload.status.value,
        features_mapped=upload.features_mapped,
        target_features_total=upload.target_features_total,
    )


# ── DQA Callback ─────────────────────────────────────────────────────────────


@router.post("/{upload_id}/mapping-callback", status_code=status.HTTP_200_OK)
async def mapping_callback(
    upload_id: str,
    body: MappingCallbackPayload,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, str]:
    """Callback endpoint for the Data Quality Agent to submit mapping results.

    This is called by the Azure Function after processing is complete.
    In production, this would be protected by a service-to-service auth token.
    """
    result = await db.execute(
        select(TrainingUpload).where(TrainingUpload.id == upload_id)
    )
    upload = result.scalar_one_or_none()
    if not upload:
        raise NotFoundError("Training upload", upload_id)

    # Clear previous mappings if any (retry scenario)
    existing = await db.execute(
        select(FieldMapping).where(FieldMapping.training_upload_id == upload.id)
    )
    for old_mapping in existing.scalars().all():
        await db.delete(old_mapping)

    # Store new mappings
    for item in body.mappings:
        # Determine confidence level
        conf = item.confidence
        if conf >= 0.90:
            level = ConfidenceLevel.GREEN
        elif conf >= 0.60:
            level = ConfidenceLevel.YELLOW
        else:
            level = ConfidenceLevel.RED

        mapping = FieldMapping(
            training_upload_id=upload.id,
            source_field=item.source_field,
            target_feature=item.target_feature,
            confidence=item.confidence,
            confidence_level=level,
            mapping_source=(
                MappingSource(item.mapping_source)
                if item.mapping_source
                else MappingSource.LLM
            ),
            sample_values=item.sample_values,
            data_type_detected=item.data_type_detected,
        )
        db.add(mapping)

    # Update upload
    upload.features_mapped = body.features_mapped
    upload.quality_report = body.quality_report
    upload.status = UploadStatus.INGESTED

    await db.flush()
    return {"status": "ok", "upload_id": upload_id}


# ── Helpers ──────────────────────────────────────────────────────────────────


async def _save_mapping_template(
    db: AsyncSession,
    upload: TrainingUpload,
    mappings: list[FieldMapping],
) -> None:
    """Save or update the mapping template for this source."""
    # Deactivate existing templates
    existing_result = await db.execute(
        select(MappingTemplate).where(
            MappingTemplate.data_source_id == upload.data_source_id,
            MappingTemplate.is_active == True,  # noqa: E712
        )
    )
    for old_template in existing_result.scalars().all():
        old_template.is_active = False

    # Get latest version number
    version_result = await db.execute(
        select(func.max(MappingTemplate.version)).where(
            MappingTemplate.data_source_id == upload.data_source_id
        )
    )
    latest_version = version_result.scalar_one() or 0

    # Create new template
    template = MappingTemplate(
        data_source_id=upload.data_source_id,
        version=latest_version + 1,
        mappings=[
            {
                "source_field": m.source_field,
                "target_feature": m.target_feature,
                "confidence": float(m.confidence),
            }
            for m in mappings
            if m.target_feature != "_unmapped"
        ],
        is_active=True,
        created_from_upload_id=upload.id,
    )
    db.add(template)

    # Update upload reference
    await db.flush()
    upload.mapping_template_id = template.id

    # Count mapped features
    mapped_targets = {
        m.target_feature for m in mappings if m.target_feature != "_unmapped"
    }
    upload.features_mapped = len(mapped_targets)
