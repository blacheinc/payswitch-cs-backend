"""Data Sources router — bank/institution registry and template access."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError, NotFoundError
from app.core.pagination import PaginatedResponse
from app.db import get_db
from app.dependencies import get_current_admin
from app.models.data_source import (
    DataSource,
    MappingTemplate,
    SourceType,
    TrainingUpload,
)
from app.models.user import AdminUser
from app.schemas.training_data import (
    CreateDataSourceRequest,
    DataSourceDetailResponse,
    DataSourceResponse,
    MappingTemplateResponse,
    TrainingUploadResponse,
)

router = APIRouter(prefix="/admin/sources", tags=["Admin – Data Sources"])


@router.post("", response_model=DataSourceResponse, status_code=status.HTTP_201_CREATED)
async def create_source(
    body: CreateDataSourceRequest,
    admin: Annotated[AdminUser, Depends(get_current_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> DataSourceResponse:
    """Register a new data source (bank/institution)."""
    existing = await db.execute(
        select(DataSource).where(DataSource.short_code == body.short_code)
    )
    if existing.scalar_one_or_none():
        raise ConflictError(
            f"Source with short_code '{body.short_code}' already exists"
        )

    source = DataSource(
        name=body.name,
        short_code=body.short_code,
        source_type=SourceType(body.source_type),
        description=body.description,
    )
    db.add(source)
    await db.flush()

    return DataSourceResponse.model_validate(source)


@router.get("", response_model=PaginatedResponse[DataSourceResponse])
async def list_sources(
    admin: Annotated[AdminUser, Depends(get_current_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=100),
    source_type: str | None = Query(None),
    search: str | None = Query(None),
) -> PaginatedResponse[DataSourceResponse]:
    """List all registered data sources."""
    query = select(DataSource)
    count_query = select(func.count(DataSource.id))

    if source_type:
        query = query.where(DataSource.source_type == SourceType(source_type))
        count_query = count_query.where(
            DataSource.source_type == SourceType(source_type)
        )
    if search:
        query = query.where(
            DataSource.name.ilike(f"%{search}%")
            | DataSource.short_code.ilike(f"%{search}%")
        )
        count_query = count_query.where(
            DataSource.name.ilike(f"%{search}%")
            | DataSource.short_code.ilike(f"%{search}%")
        )

    total = (await db.execute(count_query)).scalar_one()
    result = await db.execute(
        query.order_by(DataSource.name.asc())
        .offset((page - 1) * per_page)
        .limit(per_page)
    )
    sources = result.scalars().all()

    return PaginatedResponse.create(
        items=[DataSourceResponse.model_validate(s) for s in sources],
        total=total,
        page=page,
        per_page=per_page,
    )


@router.get("/{source_id}", response_model=DataSourceDetailResponse)
async def get_source(
    source_id: UUID,
    admin: Annotated[AdminUser, Depends(get_current_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> DataSourceDetailResponse:
    """Get source details, including template availability."""
    result = await db.execute(select(DataSource).where(DataSource.id == source_id))
    source = result.scalar_one_or_none()
    if not source:
        raise NotFoundError("Data source", str(source_id))

    # Check for active template
    template_result = await db.execute(
        select(MappingTemplate).where(
            MappingTemplate.data_source_id == source.id,
            MappingTemplate.is_active == True,  # noqa: E712
        )
    )
    active_template = template_result.scalar_one_or_none()

    resp = DataSourceDetailResponse(
        id=source.id,
        name=source.name,
        short_code=source.short_code,
        source_type=source.source_type.value,
        description=source.description,
        total_uploads=source.total_uploads,
        last_upload_at=source.last_upload_at,
        created_at=source.created_at,
        updated_at=source.updated_at,
        has_active_template=active_template is not None,
        active_template_id=active_template.id if active_template else None,
    )
    return resp


@router.get("/{source_id}/template", response_model=MappingTemplateResponse | None)
async def get_template(
    source_id: UUID,
    admin: Annotated[AdminUser, Depends(get_current_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> MappingTemplateResponse | None:
    """Get the active mapping template for a data source."""
    result = await db.execute(
        select(MappingTemplate).where(
            MappingTemplate.data_source_id == source_id,
            MappingTemplate.is_active == True,  # noqa: E712
        )
    )
    template = result.scalar_one_or_none()
    if not template:
        return None

    return MappingTemplateResponse.model_validate(template)


@router.get(
    "/{source_id}/uploads", response_model=PaginatedResponse[TrainingUploadResponse]
)
async def list_source_uploads(
    source_id: UUID,
    admin: Annotated[AdminUser, Depends(get_current_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
) -> PaginatedResponse[TrainingUploadResponse]:
    """List all uploads for a specific data source."""
    # Verify source exists
    source_result = await db.execute(
        select(DataSource).where(DataSource.id == source_id)
    )
    if not source_result.scalar_one_or_none():
        raise NotFoundError("Data source", str(source_id))

    count = (
        await db.execute(
            select(func.count(TrainingUpload.id)).where(
                TrainingUpload.data_source_id == source_id
            )
        )
    ).scalar_one()

    result = await db.execute(
        select(TrainingUpload)
        .where(TrainingUpload.data_source_id == source_id)
        .order_by(TrainingUpload.created_at.desc())
        .offset((page - 1) * per_page)
        .limit(per_page)
    )
    uploads = result.scalars().all()

    return PaginatedResponse.create(
        items=[TrainingUploadResponse.model_validate(u) for u in uploads],
        total=count,
        page=page,
        per_page=per_page,
    )
