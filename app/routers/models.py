"""Router: GET /v1/models/current — active champion model metadata.

Reads the champion metadata blob from Azure Blob Storage using managed identity.
Returns 404 if the blob doesn't exist (no training run completed yet).
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from app.dependencies import AuthContext, require_permission

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/models", tags=["Models"])


@router.get("/current")
async def get_current_champion_model(
    _auth: Annotated[AuthContext, Depends(require_permission("models.read"))],
) -> JSONResponse:
    """Return active champion model metadata.

    Reads from Azure Blob Storage (model-artifacts/champions/current.json).
    Returns 404 if no training run has completed yet.
    """
    from app.storage.champion_blob import read_champion_metadata

    try:
        metadata = read_champion_metadata()
    except Exception as e:
        logger.error(f"[models/current] Failed to read champion blob: {e}")
        return JSONResponse(
            status_code=500,
            content={
                "error": "internal_error",
                "detail": "Failed to read champion model metadata",
            },
        )

    if metadata is None:
        return JSONResponse(
            status_code=404,
            content={
                "error": "not_found",
                "detail": (
                    "Champion metadata not available yet. "
                    "No training run has completed."
                ),
            },
        )

    return JSONResponse(status_code=200, content=metadata)
