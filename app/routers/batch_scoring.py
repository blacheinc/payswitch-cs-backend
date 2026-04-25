"""Batch scoring endpoints.

POST  /v1/score/batch                   — submit a batch job
GET   /v1/score/batch                   — list batch jobs for the org
GET   /v1/score/batch/{job_id}          — per-item status counts + progress
GET   /v1/score/batch/{job_id}/results  — paginated item results
POST  /v1/score/batch/{job_id}/cancel   — cancel pending items in a queued job

New flow (v2):
  Frontend sends an array of bureau-lookup payloads (personal details).
  The backend enqueues each as a BatchJobItem in Redis and returns immediately.
  Background workers perform bureau lookup + scoring for each item internally,
  updating BatchJobItem rows as they complete.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query
from fastapi.responses import JSONResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.db.base import utcnow
from app.dependencies import AuthContext, require_any_permission, require_permission
from app.services.cross_org_access import resolve_org_filter
from app.models.batch_job_item import BatchJobItem, BatchJobItemStatus
from app.models.bulk_job import BulkJobStatus, BulkScoringJob
from app.schemas.batch_scoring import BatchScoreRequest
from app.workers.batch_worker import QUEUE_KEY

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/score/batch", tags=["Batch Scoring"])


# ── POST /v1/score/batch ──────────────────────────────────────────────────────


@router.post("")
async def submit_batch_job(
    body: BatchScoreRequest,
    auth: Annotated[AuthContext, Depends(require_permission("batch_scoring.create"))],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> JSONResponse:
    """Submit a batch scoring job.

    Accepts up to 100 applicant payloads.  Each applicant needs the same
    personal details as a bureau lookup (full_name/identification + date_of_birth).
    Returns immediately with a job_id — poll GET /v1/score/batch/{job_id} for progress.
    """
    job_id = (
        f"BATCH-{datetime.now(timezone.utc).strftime('%Y%m%d')}"
        f"-{uuid.uuid4().hex[:8].upper()}"
    )
    requested_at = utcnow()

    job = BulkScoringJob(
        job_id=job_id,
        organization_id=auth.organization_id,
        status=BulkJobStatus.QUEUED,
        total_records=len(body.items),
        processed_records=0,
        error_count=0,
        created_by_user_id=auth.user_id,
    )
    db.add(job)
    await db.flush()  # get job.id before creating items

    # Bulk-insert one BatchJobItem per array entry
    items = [
        BatchJobItem(
            batch_job_id=job.id,
            index=i,
            status=BatchJobItemStatus.PENDING,
            payload=item.model_dump(mode="json"),
        )
        for i, item in enumerate(body.items)
    ]
    db.add_all(items)
    await db.flush()

    # Push all item IDs onto the Redis queue (LPUSH = left-push, worker BRPOPs from right)
    try:
        from app.storage.cache import get_redis_cache

        client = await get_redis_cache().get_client()
        # Pipeline the pushes for efficiency
        pipe = client.pipeline()
        for item in items:
            pipe.lpush(QUEUE_KEY, str(item.id))
        await pipe.execute()
    except Exception as e:
        logger.error("[batch] Failed to enqueue items for job %s: %s", job_id, e)
        return JSONResponse(
            status_code=503,
            content={
                "error": "service_unavailable",
                "detail": "Failed to queue batch job — please retry",
            },
        )

    # Commit everything only after Redis push succeeds
    await db.commit()
    logger.info("[batch] Submitted job %s (%d items)", job_id, len(body.items))

    return JSONResponse(
        status_code=202,
        content={
            "job_id": job_id,
            "status": "queued",
            "total": len(body.items),
            "requested_at": requested_at.isoformat().replace("+00:00", "Z"),
        },
    )


# ── GET /v1/score/batch ───────────────────────────────────────────────────────


@router.get("")
async def list_batch_jobs(
    auth: Annotated[
        AuthContext,
        Depends(require_any_permission("batch_scoring.list", "admin.batch_scoring.read")),
    ],
    db: Annotated[AsyncSession, Depends(get_db)],
    status: str | None = Query(None, description="Filter by job status"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    organization_id: str | None = Query(
        None,
        description=(
            "Admin-only: scope to a specific organisation. Org users and "
            "API keys must omit or pass their own org id."
        ),
    ),
) -> JSONResponse:
    """List batch jobs.

    Org callers see their own jobs only. Platform admins with
    ``admin.batch_scoring.read`` may pass an ``organization_id`` to scope
    to a specific org, or omit it for cross-org platform visibility.
    """
    org_filter = await resolve_org_filter(
        db, auth,
        requested_org_id=organization_id,
        admin_permission="admin.batch_scoring.read",
        resource_type="batch_scoring",
    )

    q = select(BulkScoringJob).where(BulkScoringJob.job_id.is_not(None))
    if org_filter.organization_id is not None:
        q = q.where(BulkScoringJob.organization_id == org_filter.organization_id)
    if status:
        try:
            q = q.where(BulkScoringJob.status == BulkJobStatus(status))
        except ValueError:
            pass  # ignore unknown status values

    total = (
        await db.execute(select(func.count()).select_from(q.subquery()))
    ).scalar() or 0

    jobs = (
        await db.execute(
            q.order_by(BulkScoringJob.created_at.desc())
             .offset((page - 1) * page_size)
             .limit(page_size)
        )
    ).scalars().all()

    return JSONResponse(
        status_code=200,
        content={
            "items": [_job_list_item(j) for j in jobs],
            "total": total,
            "page": page,
            "page_size": page_size,
        },
    )


# ── GET /v1/score/batch/{job_id} ──────────────────────────────────────────────


@router.get("/{job_id}")
async def get_batch_job_status(
    job_id: Annotated[str, Path(description="Batch job ID")],
    auth: Annotated[
        AuthContext,
        Depends(require_any_permission("batch_scoring.read", "admin.batch_scoring.read")),
    ],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> JSONResponse:
    """Return status and per-item progress counts for a batch job.

    Org callers see only their own jobs; admins with
    ``admin.batch_scoring.read`` may inspect any org's job (audit-logged).
    """
    job = await _resolve_batch_job_for_caller(db, auth, job_id)
    if job is None:
        return JSONResponse(
            status_code=404,
            content={"error": "not_found", "detail": f"Batch job '{job_id}' not found"},
        )

    # Aggregate item counts directly from DB — no blob storage needed
    count_rows = (
        await db.execute(
            select(BatchJobItem.status, func.count())
            .where(BatchJobItem.batch_job_id == job.id)
            .group_by(BatchJobItem.status)
        )
    ).all()
    counts = {row[0]: row[1] for row in count_rows}

    pending    = counts.get(BatchJobItemStatus.PENDING, 0)
    processing = counts.get(BatchJobItemStatus.PROCESSING, 0)
    completed  = counts.get(BatchJobItemStatus.COMPLETED, 0)
    failed     = counts.get(BatchJobItemStatus.FAILED, 0)
    cancelled  = counts.get(BatchJobItemStatus.CANCELLED, 0)
    total      = job.total_records or 0
    done       = completed + failed + cancelled

    progress_pct = round(done / total * 100) if total > 0 else 0

    return JSONResponse(
        status_code=200,
        content={
            "job_id": job_id,
            "status": _map_status(job.status),
            "total": total,
            "progress_pct": progress_pct,
            "items": {
                "pending": pending,
                "processing": processing,
                "completed": completed,
                "failed": failed,
                "cancelled": cancelled,
            },
            "requested_at": job.created_at.isoformat().replace("+00:00", "Z"),
            "started_at": (
                job.started_at.isoformat().replace("+00:00", "Z")
                if job.started_at else None
            ),
            "completed_at": (
                job.completed_at.isoformat().replace("+00:00", "Z")
                if job.completed_at else None
            ),
        },
    )


# ── GET /v1/score/batch/{job_id}/results ─────────────────────────────────────


@router.get("/{job_id}/results")
async def get_batch_results(
    job_id: Annotated[str, Path(description="Batch job ID")],
    auth: Annotated[
        AuthContext,
        Depends(require_any_permission("batch_scoring.read", "admin.batch_scoring.read")),
    ],
    db: Annotated[AsyncSession, Depends(get_db)],
    status: str | None = Query(
        None,
        description="Filter items by status: completed | failed | pending | cancelled",
    ),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100),
) -> JSONResponse:
    """Return paginated per-item results for a batch job.

    Items are returned as soon as they are processed — no need to wait for the
    entire job to complete.  Use the ``status`` filter to show only failed items
    or only successful ones.

    Each completed item includes a ``score_request_id`` and ``score_tracking_id``
    the frontend can use to deep-link into the individual scoring result.

    Same org-scoping rules as the status endpoint — admins read any org,
    audit-logged.
    """
    job = await _resolve_batch_job_for_caller(db, auth, job_id)
    if job is None:
        return JSONResponse(
            status_code=404,
            content={"error": "not_found", "detail": f"Batch job '{job_id}' not found"},
        )

    q = select(BatchJobItem).where(BatchJobItem.batch_job_id == job.id)
    if status:
        try:
            q = q.where(BatchJobItem.status == BatchJobItemStatus(status))
        except ValueError:
            pass

    total = (
        await db.execute(select(func.count()).select_from(q.subquery()))
    ).scalar() or 0

    items = (
        await db.execute(
            q.order_by(BatchJobItem.index)
             .offset((page - 1) * page_size)
             .limit(page_size)
        )
    ).scalars().all()

    return JSONResponse(
        status_code=200,
        content={
            "job_id": job_id,
            "items": [_item_result(i) for i in items],
            "total": total,
            "page": page,
            "page_size": page_size,
        },
    )


# ── POST /v1/score/batch/{job_id}/cancel ─────────────────────────────────────


@router.post("/{job_id}/cancel")
async def cancel_batch_job(
    job_id: Annotated[str, Path(description="Batch job ID")],
    auth: Annotated[AuthContext, Depends(require_permission("batch_scoring.cancel"))],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> JSONResponse:
    """Cancel a batch job.

    All PENDING items are marked CANCELLED immediately.  Items currently being
    processed (status=processing) will finish naturally — the worker checks the
    status before starting and skips non-PENDING items.

    Only jobs in 'queued' or 'processing' state can be cancelled.
    """
    job = await _get_job_or_404(db, job_id, auth.organization_id)
    if job is None:
        return JSONResponse(
            status_code=404,
            content={"error": "not_found", "detail": f"Batch job '{job_id}' not found"},
        )

    if job.status not in (BulkJobStatus.QUEUED, BulkJobStatus.PROCESSING):
        return JSONResponse(
            status_code=409,
            content={
                "error": "conflict",
                "detail": (
                    f"Job cannot be cancelled — current status is "
                    f"'{job.status.value}'. Only queued or processing jobs can be cancelled."
                ),
            },
        )

    # Cancel all pending items in bulk
    pending_items = (
        await db.execute(
            select(BatchJobItem).where(
                BatchJobItem.batch_job_id == job.id,
                BatchJobItem.status == BatchJobItemStatus.PENDING,
            )
        )
    ).scalars().all()

    cancelled_count = 0
    for item in pending_items:
        item.status = BatchJobItemStatus.CANCELLED
        cancelled_count += 1

    job.status = BulkJobStatus.CANCELLED
    await db.commit()

    logger.info(
        "[batch] Job %s cancelled by %s — %d pending items cancelled",
        job_id, auth.user_id or "api_key", cancelled_count,
    )

    return JSONResponse(
        status_code=200,
        content={
            "job_id": job_id,
            "status": "cancelled",
            "cancelled_items": cancelled_count,
        },
    )


# ── Helpers ───────────────────────────────────────────────────────────────────


async def _get_job_or_404(
    db: AsyncSession,
    job_id: str,
    organization_id,
) -> BulkScoringJob | None:
    """Strict org-scoped job lookup, used by mutating endpoints (cancel)."""
    q = select(BulkScoringJob).where(BulkScoringJob.job_id == job_id)
    if organization_id:
        q = q.where(BulkScoringJob.organization_id == organization_id)
    return (await db.execute(q)).scalar_one_or_none()


async def _resolve_batch_job_for_caller(
    db: AsyncSession,
    auth: AuthContext,
    job_id: str,
) -> BulkScoringJob | None:
    """Look up a batch job honouring cross-org admin reads.

    Org callers may only fetch jobs that belong to their own
    ``organization_id``.  Platform admins with the
    ``admin.batch_scoring.read`` permission may fetch any job — those
    accesses are recorded in ``audit_logs``.
    """
    q = select(BulkScoringJob).where(BulkScoringJob.job_id == job_id)
    if auth.user_type != "admin":
        q = q.where(BulkScoringJob.organization_id == auth.organization_id)

    job = (await db.execute(q)).scalar_one_or_none()
    if job is None:
        return None

    if auth.user_type == "admin":
        from app.models.audit import AuditLog

        db.add(AuditLog(
            actor_type="admin",
            actor_id=str(auth.user_id) if auth.user_id else None,
            actor_email=auth.user_email,
            organization_id=str(job.organization_id) if job.organization_id else None,
            action="batch_scoring.read_cross_org",
            resource_type="batch_scoring",
            resource_id=job_id,
            description=f"Admin viewed batch job {job_id}",
        ))

    return job


def _map_status(status: BulkJobStatus) -> str:
    return {
        BulkJobStatus.QUEUED: "queued",
        BulkJobStatus.PROCESSING: "processing",
        BulkJobStatus.COMPLETED: "completed",
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
        "requested_at": job.created_at.isoformat().replace("+00:00", "Z"),
        "completed_at": (
            job.completed_at.isoformat().replace("+00:00", "Z")
            if job.completed_at else None
        ),
    }


def _item_result(item: BatchJobItem) -> dict:
    return {
        "index": item.index,
        "status": item.status.value,
        "score_request_id": str(item.score_request_id) if item.score_request_id else None,
        "score_tracking_id": item.score_tracking_id,
        "error_code": item.error_code,
        "error_message": item.error_message,
        "payload": item.payload,
        "completed_at": (
            item.completed_at.isoformat().replace("+00:00", "Z")
            if item.completed_at else None
        ),
    }
