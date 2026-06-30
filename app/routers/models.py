"""Router: GET /v1/models/current — active champion model metadata.

Reads the champion metadata blob from Azure Blob Storage using managed identity.
Returns 404 if the blob doesn't exist (no training run completed yet).
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.dependencies import AuthContext, require_permission

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/models", tags=["Models"])


async def _champions_from_db(db: AsyncSession) -> dict | None:
    """Build champion metadata from the model_versions table.

    Fallback for when the champion blob is unavailable (e.g. object storage
    not configured). A champion is the most recently promoted PRODUCTION
    version per model_type. Mirrors the shape of champions/current.json.
    """
    from sqlalchemy import select

    from app.models.model_version import ModelStatus, ModelVersion

    rows = (
        (
            await db.execute(
                select(ModelVersion)
                .where(ModelVersion.status == ModelStatus.PRODUCTION)
                .order_by(ModelVersion.promoted_at.desc().nullslast())
            )
        )
        .scalars()
        .all()
    )
    if not rows:
        return None

    models = []
    latest = None
    seen: set[str] = set()
    for mv in rows:
        if mv.model_type in seen:
            continue
        seen.add(mv.model_type)
        ts = mv.promoted_at or mv.created_at
        if ts and (latest is None or ts > latest):
            latest = ts
        models.append(
            {
                "model_type": mv.model_type,
                "registry_name": f"{mv.model_type}_model",
                "version": str(mv.version),
                "status": mv.status.value if hasattr(mv.status, "value") else mv.status,
                "created_at": (
                    mv.created_at.isoformat().replace("+00:00", "Z") if mv.created_at else None
                ),
                "promoted_at": (
                    mv.promoted_at.isoformat().replace("+00:00", "Z") if mv.promoted_at else None
                ),
                "metrics": mv.metrics or {},
                "feature_count": mv.feature_count,
                "training_id": mv.training_id,
            }
        )

    return {
        "updated_at": (latest.isoformat().replace("+00:00", "Z") if latest else None),
        "source": "model_versions",
        "models": models,
    }


@router.get("/current")
async def get_current_champion_model(
    _auth: Annotated[AuthContext, Depends(require_permission("models.read"))],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> JSONResponse:
    """Return active champion model metadata.

    Prefers the champion blob (model-artifacts/champions/current.json) and
    falls back to the model_versions table when the blob is unavailable.
    Returns 404 only when neither source has any champion.
    """
    from app.storage.champion_blob import read_champion_metadata

    try:
        metadata = read_champion_metadata()
    except Exception as e:
        logger.error(f"[models/current] Failed to read champion blob: {e}")
        metadata = None

    if metadata is None:
        metadata = await _champions_from_db(db)

    if metadata is None:
        return JSONResponse(
            status_code=404,
            content={
                "error": "not_found",
                "detail": ("Champion metadata not available yet. No training run has completed."),
            },
        )

    return JSONResponse(status_code=200, content=metadata)
