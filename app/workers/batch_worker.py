"""Batch scoring worker — Redis queue consumer.

Architecture
------------
N independent worker coroutines each run an infinite pop-process loop:

    while True:
        item_id = BRPOP batch:queue (blocks up to POLL_TIMEOUT seconds)
        process item  (bureau lookup → hard-stops → DE inference)

Concurrency is naturally capped at N (default 5).  Workers start with a
staggered delay (100 ms × worker_id) so they don't all hammer the bureau
API simultaneously when the queue already has items at startup.

Per-item processing
-------------------
1. Bureau lookup  — calls XDS with the payload stored on the BatchJobItem.
2. Hard-stop eval — uses the same rules as the single score request flow.
3. ScoreRequest   — created regardless of hard-stop outcome:
     hard-stop triggered → status COMPLETED + ScoringResult(DECLINE)
     normal flow         → status PROCESSING + fire DE inference
4. BatchJobItem.score_request_id is set and status → COMPLETED on success,
   or → FAILED with an error_code on any unrecoverable error.
5. After every item the job-level status / counters are re-evaluated.
"""

from __future__ import annotations

import asyncio
import logging
import random
import uuid
from datetime import date

logger = logging.getLogger(__name__)

QUEUE_KEY = "batch:queue"
POLL_TIMEOUT = 5       # seconds to block on BRPOP before looping again
WORKER_COUNT = 5       # concurrent workers — tune for XDS rate limits
STARTUP_STAGGER = 0.1  # seconds between worker starts (anti thundering-herd)
PRE_BUREAU_JITTER = (0.05, 0.4)  # random sleep range before each bureau call


# ── Public entry point ────────────────────────────────────────────────────────


def start_batch_workers() -> list[asyncio.Task]:
    """Create WORKER_COUNT background tasks and return them.

    Call once from the FastAPI lifespan startup hook.
    The tasks run until the process exits or they are explicitly cancelled.
    """
    tasks = [
        asyncio.create_task(
            _worker_loop(worker_id=i),
            name=f"batch-worker-{i}",
        )
        for i in range(WORKER_COUNT)
    ]
    logger.info("[batch_worker] Started %d workers (queue key: %s)", WORKER_COUNT, QUEUE_KEY)
    return tasks


# ── Worker loop ───────────────────────────────────────────────────────────────


async def _worker_loop(worker_id: int) -> None:
    """Long-running coroutine: pop one item at a time and process it."""
    # Stagger startup so workers don't all try to pop simultaneously
    await asyncio.sleep(worker_id * STARTUP_STAGGER)

    from app.storage.cache import get_redis_cache

    cache = get_redis_cache()

    while True:
        try:
            client = await cache.get_client()
            result = await client.brpop(QUEUE_KEY, timeout=POLL_TIMEOUT)
            if result is None:
                # Timeout — nothing in queue, loop back
                continue

            _, raw = result
            item_id = raw if isinstance(raw, str) else raw.decode()

            await _process_item(item_id)

        except asyncio.CancelledError:
            logger.info("[batch_worker] Worker %d cancelled — shutting down", worker_id)
            raise
        except Exception:
            logger.exception(
                "[batch_worker] Worker %d unhandled error — backing off 2 s", worker_id
            )
            await asyncio.sleep(2)


# ── Item processor ────────────────────────────────────────────────────────────


async def _process_item(item_id: str) -> None:  # noqa: C901 (complex but cohesive)
    """Process one batch item end-to-end inside its own DB session."""
    from app.db import async_session_factory
    from app.db.base import utcnow
    from app.models.batch_job_item import BatchJobItem, BatchJobItemStatus
    from app.models.bulk_job import BulkJobStatus, BulkScoringJob
    from app.models.score_request import RequestSource, ScoreRequest, ScoreRequestStatus
    from app.models.scoring_result import ScoringDecision, ScoringResult
    from app.services.hard_stops import evaluate_hard_stops
    from app.services.scoring_pipeline import (
        build_de_payload,
        extract_hard_stop_signals,
        fire_de_inference,
    )
    from app.services.xds_client import XDSError, get_xds_client
    from sqlalchemy import func, select

    async with async_session_factory() as db:
        # ── 1. Fetch item ────────────────────────────────────────────────
        item: BatchJobItem | None = (
            await db.execute(select(BatchJobItem).where(BatchJobItem.id == item_id))
        ).scalar_one_or_none()

        if item is None:
            logger.warning("[batch_worker] Item %s not found — skipping", item_id)
            return

        # Idempotency guard — skip if another worker already grabbed it
        if item.status not in (BatchJobItemStatus.PENDING,):
            logger.debug(
                "[batch_worker] Item %s already in status %s — skipping",
                item_id, item.status.value,
            )
            return

        # ── 2. Fetch parent job ──────────────────────────────────────────
        job: BulkScoringJob | None = (
            await db.execute(
                select(BulkScoringJob).where(BulkScoringJob.id == item.batch_job_id)
            )
        ).scalar_one_or_none()

        if job is None:
            logger.warning("[batch_worker] Job for item %s not found — skipping", item_id)
            return

        # ── 3. Mark item → processing ────────────────────────────────────
        item.status = BatchJobItemStatus.PROCESSING
        item.started_at = utcnow()

        if job.status == BulkJobStatus.QUEUED:
            job.status = BulkJobStatus.PROCESSING
            job.started_at = utcnow()

        await db.commit()

        # ── 4. Bureau lookup ─────────────────────────────────────────────
        payload = item.payload  # dict from JSONB

        # Small jitter to spread concurrent bureau calls across workers
        await asyncio.sleep(random.uniform(*PRE_BUREAU_JITTER))

        try:
            dob_raw = payload.get("date_of_birth")
            if isinstance(dob_raw, str):
                from datetime import datetime as _dt
                dob = _dt.fromisoformat(dob_raw).date()
            else:
                dob = dob_raw  # already a date object (shouldn't happen from JSONB)

            xds = get_xds_client()
            raw_xds = await xds.full_bureau_lookup(
                date_of_birth=dob,
                consumer_name=payload.get("full_name"),
                identification=payload.get("identification"),
                phone_number=payload.get("phone_number"),
                account_number=payload.get("account_number"),
                enquiry_reason=payload.get(
                    "enquiry_reason", "Application for credit by a borrower"
                ),
            )
        except XDSError as e:
            logger.warning(
                "[batch_worker] Bureau lookup failed for item %s: %s", item_id, e.message
            )
            await _fail_item(db, item, job, "bureau_failed", e.message)
            return
        except Exception as e:
            logger.exception(
                "[batch_worker] Unexpected error during bureau lookup for item %s", item_id
            )
            await _fail_item(db, item, job, "bureau_failed", str(e))
            return

        # ── 5. Hard-stop evaluation ──────────────────────────────────────
        applicant_age, identity_confidence, bureau_data = extract_hard_stop_signals(
            raw_xds, date_of_birth=dob
        )
        hard_stop = evaluate_hard_stops(
            applicant_age=applicant_age,
            identity_confidence=identity_confidence,
            bureau_data=bureau_data,
        )

        # ── 6. Create ScoreRequest ───────────────────────────────────────
        tracking_id = f"SCR-{uuid.uuid4().hex[:12].upper()}"

        try:
            if hard_stop.passed:
                # Normal flow — submit to DE
                score_req = ScoreRequest(
                    tracking_id=tracking_id,
                    organization_id=job.organization_id,
                    status=ScoreRequestStatus.PROCESSING,
                    request_source=RequestSource.BULK,
                    request_payload=payload,
                    created_by_user_id=str(job.created_by_user_id) if job.created_by_user_id else None,
                )
                db.add(score_req)
                await db.flush()  # get score_req.id

                de_payload = build_de_payload(tracking_id, raw_xds)
                try:
                    await fire_de_inference(tracking_id, de_payload)
                except Exception as de_err:
                    # DE call failed — mark ScoreRequest as FAILED and fail the item
                    score_req.status = ScoreRequestStatus.FAILED

                    error_result = ScoringResult(
                        request_id=tracking_id,
                        score_request_id=score_req.id,
                        scoring_timestamp=utcnow(),
                        decision=ScoringDecision.ERROR,
                        condition_applied=[],
                        scoring_metadata=None,
                        errors=[str(de_err)],
                    )
                    db.add(error_result)
                    await _fail_item(
                        db, item, job, "scoring_error",
                        f"DE inference failed: {de_err}",
                        score_req_id=score_req.id,
                        tracking_id=tracking_id,
                    )
                    return

            else:
                # Hard-stop triggered — immediate DECLINE
                score_req = ScoreRequest(
                    tracking_id=tracking_id,
                    organization_id=job.organization_id,
                    status=ScoreRequestStatus.COMPLETED,
                    request_source=RequestSource.BULK,
                    request_payload=payload,
                    created_by_user_id=str(job.created_by_user_id) if job.created_by_user_id else None,
                    scored_at=utcnow(),
                )
                db.add(score_req)
                await db.flush()

                decline_result = ScoringResult(
                    request_id=tracking_id,
                    score_request_id=score_req.id,
                    scoring_timestamp=utcnow(),
                    decision=ScoringDecision.DECLINE,
                    condition_applied=[],
                    credit_risk=None,
                    fraud_detection=None,
                    loan_amount=None,
                    income_verification=None,
                    scoring_metadata={
                        "hard_stop": True,
                        "triggered_rules": hard_stop.triggered_rules,
                        "decline_reasons": hard_stop.decline_reasons,
                    },
                    errors=None,
                )
                db.add(decline_result)

        except Exception as e:
            logger.exception(
                "[batch_worker] Error creating ScoreRequest for item %s", item_id
            )
            await _fail_item(db, item, job, "scoring_error", str(e))
            return

        # ── 7. Mark item completed ───────────────────────────────────────
        item.status = BatchJobItemStatus.COMPLETED
        item.score_request_id = str(score_req.id)
        item.score_tracking_id = tracking_id
        item.completed_at = utcnow()

        job.processed_records = (job.processed_records or 0) + 1
        await _maybe_finalize_job(db, job)

        await db.commit()
        logger.info(
            "[batch_worker] Item %s completed → ScoreRequest %s (hard_stop=%s)",
            item_id, tracking_id, not hard_stop.passed,
        )


# ── Helpers ───────────────────────────────────────────────────────────────────


async def _fail_item(
    db,
    item,
    job,
    error_code: str,
    error_message: str,
    score_req_id: str | None = None,
    tracking_id: str | None = None,
) -> None:
    """Mark item as FAILED and update job counters."""
    from app.db.base import utcnow
    from app.models.batch_job_item import BatchJobItemStatus

    item.status = BatchJobItemStatus.FAILED
    item.error_code = error_code
    item.error_message = error_message[:1000] if error_message else None
    item.completed_at = utcnow()
    if score_req_id:
        item.score_request_id = score_req_id
    if tracking_id:
        item.score_tracking_id = tracking_id

    job.error_count = (job.error_count or 0) + 1
    await _maybe_finalize_job(db, job)

    await db.commit()
    logger.warning(
        "[batch_worker] Item %s failed — %s: %s",
        item.id, error_code, error_message,
    )


async def _maybe_finalize_job(db, job) -> None:
    """Check if all items are terminal and update the job status accordingly.

    Must be called *before* db.commit() so the caller commits everything in one shot.
    """
    from app.db.base import utcnow
    from app.models.batch_job_item import BatchJobItem, BatchJobItemStatus
    from app.models.bulk_job import BulkJobStatus
    from sqlalchemy import func, select

    terminal = {BatchJobItemStatus.COMPLETED, BatchJobItemStatus.FAILED, BatchJobItemStatus.CANCELLED}

    counts = (
        await db.execute(
            select(BatchJobItem.status, func.count())
            .where(BatchJobItem.batch_job_id == job.id)
            .group_by(BatchJobItem.status)
        )
    ).all()

    status_map = {row[0]: row[1] for row in counts}
    total_terminal = sum(
        v for k, v in status_map.items() if k in terminal
    )

    if total_terminal < (job.total_records or 0):
        return  # still items in flight

    # All items done
    failed = status_map.get(BatchJobItemStatus.FAILED, 0)
    completed = status_map.get(BatchJobItemStatus.COMPLETED, 0)

    job.completed_at = utcnow()
    if completed == 0:
        job.status = BulkJobStatus.FAILED
    else:
        # Any mix of completed + failed → COMPLETED (error_count shows failures)
        job.status = BulkJobStatus.COMPLETED

    logger.info(
        "[batch_worker] Job %s finalized → %s (completed=%d failed=%d)",
        job.job_id, job.status.value, completed, failed,
    )
