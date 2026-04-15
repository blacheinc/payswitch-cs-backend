"""Batch scoring endpoints.

POST  /v1/score/batch                   — submit a batch job
GET   /v1/score/batch                   — list batch jobs for the org
GET   /v1/score/batch/{jobId}           — job status + progress
GET   /v1/score/batch/{jobId}/results   — paginated results table (DB)
POST  /v1/score/batch/{jobId}/cancel    — cancel a queued job

Auth: get_auth_context() — org API key or org user JWT.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query
from fastapi.responses import JSONResponse
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.db.base import utcnow
from app.dependencies import AuthContext, get_auth_context
from app.models.bulk_job import BulkJobStatus, BulkScoringJob, IngestionStatus
from app.models.scoring_result import ScoringResult
from app.schemas.batch_scoring import BatchScoreRequest

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/score/batch", tags=["Batch Scoring"])


# ── POST /v1/score/batch ──────────────────────────────────────────────────────

@router.post("")
async def submit_batch_job(
    body: BatchScoreRequest,
    auth: Annotated[AuthContext, Depends(get_auth_context)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> JSONResponse:
    """Submit a batch scoring job.

    Persists a BulkScoringJob record and publishes the job to the
    batch-score-request Service Bus topic. The orchestrator picks it up
    asynchronously and writes progress to blob storage.
    """
    if not auth.organization_id:
        return JSONResponse(
            status_code=403,
            content={"error": "forbidden", "detail": "Organization context required"},
        )

    job_id = (
        f"BATCH-{datetime.now(timezone.utc).strftime('%Y%m%d')}"
        f"-{uuid.uuid4().hex[:8].upper()}"
    )
    requested_at = utcnow()

    job = BulkScoringJob(
        job_id=job_id,
        organization_id=auth.organization_id,
        status=BulkJobStatus.QUEUED,
        models_to_run=body.models_to_run,
        total_records=len(body.applicants),
        ingestion_status=IngestionStatus.PENDING,
        created_by_user_id=auth.user_id,
    )
    db.add(job)
    await db.flush()  # get job.id before publishing

    # Publish to Service Bus
    try:
        from app.core.events import get_event_publisher

        await get_event_publisher().publish_batch_scoring_job({
            "job_id": job_id,
            "requested_at": requested_at.isoformat().replace("+00:00", "Z"),
            "models_to_run": body.models_to_run,
            "applicants": [a.model_dump() for a in body.applicants],
        })
    except Exception as e:
        logger.error("[batch] Failed to publish job %s to Service Bus: %s", job_id, e)
        return JSONResponse(
            status_code=503,
            content={
                "error": "service_unavailable",
                "detail": "Failed to queue batch job — please retry",
            },
        )

    await db.commit()
    logger.info("[batch] Submitted job %s (%d applicants)", job_id, len(body.applicants))

    return JSONResponse(
        status_code=202,
        content={
            "job_id": job_id,
            "status": "queued",
            "total": len(body.applicants),
            "requested_at": requested_at.isoformat().replace("+00:00", "Z"),
        },
    )


# ── GET /v1/score/batch ───────────────────────────────────────────────────────

@router.get("")
async def list_batch_jobs(
    auth: Annotated[AuthContext, Depends(get_auth_context)],
    db: Annotated[AsyncSession, Depends(get_db)],
    status: str | None = Query(None, description="Filter by status"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
) -> JSONResponse:
    """List all batch jobs for the authenticated organization."""
    if not auth.organization_id:
        return JSONResponse(
            status_code=403,
            content={"error": "forbidden", "detail": "Organization context required"},
        )

    q = select(BulkScoringJob).where(
        BulkScoringJob.organization_id == auth.organization_id,
        BulkScoringJob.job_id.is_not(None),  # only API-submitted batch jobs
    )
    if status:
        q = q.where(BulkScoringJob.status == status)

    total = (await db.execute(
        select(func.count()).select_from(q.subquery())
    )).scalar() or 0

    jobs = (await db.execute(
        q.order_by(BulkScoringJob.created_at.desc())
         .offset((page - 1) * page_size)
         .limit(page_size)
    )).scalars().all()

    return JSONResponse(
        status_code=200,
        content={
            "items": [_job_list_item(j) for j in jobs],
            "total": total,
            "page": page,
            "page_size": page_size,
        },
    )


# ── GET /v1/score/batch/{jobId} ───────────────────────────────────────────────

@router.get("/{job_id}")
async def get_batch_job_status(
    job_id: Annotated[str, Path(description="Batch job ID")],
    auth: Annotated[AuthContext, Depends(get_auth_context)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> JSONResponse:
    """Return status and live progress for a batch job.

    While the job is in_progress, the orchestrator keeps
    model-artifacts/batch/{job_id}/status.json updated.
    This endpoint merges that blob data with our DB record.
    """
    job = await _get_job_or_404(db, job_id, auth.organization_id)
    if job is None:
        return JSONResponse(
            status_code=404,
            content={"error": "not_found", "detail": f"Batch job '{job_id}' not found"},
        )

    # Merge with live blob status (authoritative for in-flight progress)
    blob_status = None
    if job.status in (BulkJobStatus.QUEUED, BulkJobStatus.PROCESSING):
        try:
            from app.storage.batch_blob import read_batch_status
            blob_status = read_batch_status(job_id)
        except Exception as e:
            logger.warning("[batch] Could not read blob status for %s: %s", job_id, e)

    total = job.total_records
    completed = job.processed_records
    failed = job.error_count
    updated_at = None

    if blob_status:
        total = blob_status.get("total", total)
        completed = blob_status.get("completed", completed)
        failed = blob_status.get("failed", failed)
        updated_at = blob_status.get("updated_at")

        # Sync status from blob if orchestrator has moved it forward
        blob_job_status = blob_status.get("status", "")
        if blob_job_status == "in_progress" and job.status == BulkJobStatus.QUEUED:
            job.status = BulkJobStatus.PROCESSING
            await db.commit()

    progress_pct = (
        round(completed / total * 100) if total and total > 0 else 0
    )

    return JSONResponse(
        status_code=200,
        content={
            "job_id": job_id,
            "status": _map_status(job.status),
            "total": total,
            "completed": completed,
            "failed": failed,
            "progress_pct": progress_pct,
            "ingestion_status": job.ingestion_status.value,
            "ingested_count": job.ingested_count,
            "requested_at": job.created_at.isoformat().replace("+00:00", "Z"),
            "updated_at": updated_at,
            "completed_at": (
                job.completed_at.isoformat().replace("+00:00", "Z")
                if job.completed_at else None
            ),
        },
    )


# ── GET /v1/score/batch/{jobId}/results ──────────────────────────────────────

@router.get("/{job_id}/results")
async def get_batch_results(
    job_id: Annotated[str, Path(description="Batch job ID")],
    auth: Annotated[AuthContext, Depends(get_auth_context)],
    db: Annotated[AsyncSession, Depends(get_db)],
    decision: str | None = Query(None, description="Filter by decision outcome"),
    risk_tier: str | None = Query(None, description="Filter by risk tier"),
    score_grade: str | None = Query(None, description="Filter by score grade"),
    sort_by: str = Query("scoring_timestamp", pattern="^(credit_score|probability_of_default|scoring_timestamp)$"),
    order: str = Query("desc", pattern="^(asc|desc)$"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
) -> JSONResponse:
    """Return paginated scoring results for a completed batch job.

    Results are only available once ingestion_status == "complete".
    While ingestion is running, the response will indicate the current
    ingested_count so the frontend can show a progress state.
    """
    job = await _get_job_or_404(db, job_id, auth.organization_id)
    if job is None:
        return JSONResponse(
            status_code=404,
            content={"error": "not_found", "detail": f"Batch job '{job_id}' not found"},
        )

    if job.ingestion_status == IngestionStatus.PENDING:
        return JSONResponse(
            status_code=200,
            content={
                "job_id": job_id,
                "ingestion_status": "pending",
                "items": [],
                "total": 0,
                "page": page,
                "page_size": page_size,
            },
        )

    # Build results query
    q = select(ScoringResult).where(ScoringResult.bulk_job_id == job.id)

    # Filters on JSONB fields
    if decision:
        q = q.where(ScoringResult.decision == decision)
    if risk_tier:
        q = q.where(
            ScoringResult.credit_risk["risk_tier"].as_string() == risk_tier
        )
    if score_grade:
        q = q.where(
            ScoringResult.scoring_metadata["score_grade"].as_string() == score_grade
        )

    total = (await db.execute(
        select(func.count()).select_from(q.subquery())
    )).scalar() or 0

    # Sorting — map friendly names to column/JSONB expressions
    sort_col = {
        "scoring_timestamp": ScoringResult.scoring_timestamp,
        "credit_score": ScoringResult.scoring_metadata["credit_score"].as_integer(),
        "probability_of_default": ScoringResult.credit_risk["probability_of_default"].as_float(),
    }[sort_by]

    if order == "desc":
        sort_col = sort_col.desc()

    results = (await db.execute(
        q.order_by(sort_col)
         .offset((page - 1) * page_size)
         .limit(page_size)
    )).scalars().all()

    return JSONResponse(
        status_code=200,
        content={
            "job_id": job_id,
            "ingestion_status": job.ingestion_status.value,
            "items": [_result_item(r) for r in results],
            "total": total,
            "page": page,
            "page_size": page_size,
        },
    )


# ── POST /v1/score/batch/{jobId}/cancel ──────────────────────────────────────

@router.post("/{job_id}/cancel")
async def cancel_batch_job(
    job_id: Annotated[str, Path(description="Batch job ID")],
    auth: Annotated[AuthContext, Depends(get_auth_context)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> JSONResponse:
    """Cancel a queued batch job.

    Only possible while status is 'queued' — once the orchestrator has
    started processing, cancellation is no longer available.
    """
    job = await _get_job_or_404(db, job_id, auth.organization_id)
    if job is None:
        return JSONResponse(
            status_code=404,
            content={"error": "not_found", "detail": f"Batch job '{job_id}' not found"},
        )

    if job.status != BulkJobStatus.QUEUED:
        return JSONResponse(
            status_code=409,
            content={
                "error": "conflict",
                "detail": (
                    f"Job cannot be cancelled — current status is '{job.status.value}'. "
                    "Only queued jobs can be cancelled."
                ),
            },
        )

    job.status = BulkJobStatus.CANCELLED
    await db.commit()
    logger.info("[batch] Job %s cancelled by %s", job_id, auth.user_id or "api_key")

    return JSONResponse(
        status_code=200,
        content={"job_id": job_id, "status": "cancelled"},
    )


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _get_job_or_404(
    db: AsyncSession,
    job_id: str,
    organization_id,
) -> BulkScoringJob | None:
    """Fetch a BulkScoringJob scoped to the organization."""
    q = select(BulkScoringJob).where(
        BulkScoringJob.job_id == job_id,
    )
    if organization_id:
        q = q.where(BulkScoringJob.organization_id == organization_id)
    return (await db.execute(q)).scalar_one_or_none()


def _map_status(status: BulkJobStatus) -> str:
    return {
        BulkJobStatus.QUEUED: "queued",
        BulkJobStatus.PROCESSING: "in_progress",
        BulkJobStatus.COMPLETED: "complete",
        BulkJobStatus.FAILED: "failed",
        BulkJobStatus.CANCELLED: "cancelled",
        BulkJobStatus.VALIDATING: "validating",
    }.get(status, status.value)


def _job_list_item(job: BulkScoringJob) -> dict:
    return {
        "job_id": job.job_id,
        "status": _map_status(job.status),
        "total": job.total_records,
        "completed": job.processed_records,
        "failed": job.error_count,
        "ingestion_status": job.ingestion_status.value,
        "requested_at": job.created_at.isoformat().replace("+00:00", "Z"),
        "completed_at": (
            job.completed_at.isoformat().replace("+00:00", "Z")
            if job.completed_at else None
        ),
    }


def _result_item(r: ScoringResult) -> dict:
    credit_risk = r.credit_risk or {}
    loan = r.loan_amount or {}
    fraud = r.fraud_detection or {}
    meta = r.scoring_metadata or {}
    return {
        "request_id": r.request_id,
        "decision": r.decision.value if r.decision else None,
        "credit_score": meta.get("credit_score"),
        "score_grade": meta.get("score_grade"),
        "risk_tier": credit_risk.get("risk_tier"),
        "probability_of_default": credit_risk.get("probability_of_default"),
        "recommended_amount_ghs": loan.get("recommended_amount_ghs"),
        "fraud_risk_flag": fraud.get("fraud_risk_flag"),
        "scoring_timestamp": (
            r.scoring_timestamp.isoformat().replace("+00:00", "Z")
            if r.scoring_timestamp else None
        ),
    }
