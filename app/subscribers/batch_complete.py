"""Subscriber for the batch-score-complete Service Bus topic.

When the orchestrator finishes a batch job it publishes a completion event here.
This handler:
  1. Updates the BulkScoringJob record (status, counts, timestamps)
  2. Ingests results.jsonl into ScoringResult rows (so the results table works)
  3. Triggers a webhook if the org has one configured

Subscription: backend-sub
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

from app.db import async_session_factory
from app.db.base import utcnow
from app.models.bulk_job import BulkJobStatus, BulkScoringJob, IngestionStatus
from app.models.scoring_result import ScoringDecision, ScoringResult

logger = logging.getLogger(__name__)


async def handle_batch_score_complete(message: dict[str, Any]) -> None:
    """Process a batch-score-complete message.

    Expected payload:
        job_id, completed_at, total, succeeded, failed, results_blob_path
    """
    job_id = message.get("job_id")
    if not job_id:
        logger.error("[batch-complete] Missing job_id in message")
        return

    logger.info("[batch-complete] Processing completion for job %s", job_id)

    async with async_session_factory() as db:
        async with db.begin():
            job = (await db.execute(
                select(BulkScoringJob).where(BulkScoringJob.job_id == job_id)
            )).scalar_one_or_none()

            if not job:
                logger.warning("[batch-complete] BulkScoringJob not found for %s", job_id)
                return

            # Idempotency — already processed
            if job.status == BulkJobStatus.COMPLETED:
                logger.info("[batch-complete] Job %s already completed, skipping", job_id)
                return

            # Parse completion timestamp
            completed_at = None
            if message.get("completed_at"):
                try:
                    completed_at = datetime.fromisoformat(
                        message["completed_at"].replace("Z", "+00:00")
                    )
                except (ValueError, TypeError):
                    completed_at = utcnow()

            succeeded = message.get("succeeded", 0)
            failed = message.get("failed", 0)
            results_blob_path = message.get("results_blob_path")

            job.status = BulkJobStatus.COMPLETED
            job.processed_records = succeeded
            job.error_count = failed
            job.results_blob_path = results_blob_path
            job.completed_at = completed_at or utcnow()
            job.ingestion_status = IngestionStatus.INGESTING

    # ── Ingest results into ScoringResult rows ────────────────────────────
    # Run outside the status-update transaction so a partial ingest failure
    # doesn't roll back the COMPLETED status update.
    if results_blob_path:
        await _ingest_results(job_id, results_blob_path)
    else:
        logger.warning("[batch-complete] No results_blob_path in message for %s", job_id)
        await _mark_ingestion(job_id, IngestionStatus.FAILED)


async def _ingest_results(job_id: str, blob_path: str) -> None:
    """Read results.jsonl and write each line as a ScoringResult row."""
    ingested = 0
    errors = 0

    try:
        from app.storage.batch_blob import stream_batch_results

        async with async_session_factory() as db:
            # Fetch the job record for its UUID primary key
            job = (await db.execute(
                select(BulkScoringJob).where(BulkScoringJob.job_id == job_id)
            )).scalar_one_or_none()

            if not job:
                logger.error("[batch-ingest] BulkScoringJob %s not found", job_id)
                return

            bulk_job_uuid = job.id

        # Stream JSONL — one ScoringResponse per line
        for scoring_response in stream_batch_results(blob_path):
            request_id = scoring_response.get("request_id")
            decision_value = scoring_response.get("decision")

            if not request_id or not decision_value:
                errors += 1
                continue

            try:
                decision = ScoringDecision(decision_value)
            except ValueError:
                logger.warning("[batch-ingest] Unknown decision '%s' for %s", decision_value, request_id)
                errors += 1
                continue

            # Parse scoring timestamp
            scoring_ts = None
            if scoring_response.get("scoring_timestamp"):
                try:
                    scoring_ts = datetime.fromisoformat(
                        scoring_response["scoring_timestamp"].replace("Z", "+00:00")
                    )
                except (ValueError, TypeError):
                    scoring_ts = datetime.now(timezone.utc)

            async with async_session_factory() as db:
                async with db.begin():
                    # Idempotency — skip if already exists
                    existing = (await db.execute(
                        select(ScoringResult.id).where(ScoringResult.request_id == request_id)
                    )).scalar_one_or_none()
                    if existing:
                        ingested += 1
                        continue

                    db.add(ScoringResult(
                        request_id=request_id,
                        bulk_job_id=bulk_job_uuid,
                        scoring_timestamp=scoring_ts,
                        decision=decision,
                        condition_applied=scoring_response.get("condition_applied", []),
                        credit_risk=scoring_response.get("credit_risk"),
                        fraud_detection=scoring_response.get("fraud_detection"),
                        loan_amount=scoring_response.get("loan_amount"),
                        income_verification=scoring_response.get("income_verification"),
                        scoring_metadata=scoring_response.get("scoring_metadata"),
                        errors=scoring_response.get("refer_reasons"),
                    ))
                    ingested += 1

        # Update ingested_count and mark complete
        async with async_session_factory() as db:
            async with db.begin():
                job = (await db.execute(
                    select(BulkScoringJob).where(BulkScoringJob.job_id == job_id)
                )).scalar_one_or_none()
                if job:
                    job.ingested_count = ingested
                    job.ingestion_status = IngestionStatus.COMPLETE

        logger.info(
            "[batch-ingest] Job %s: ingested=%d errors=%d", job_id, ingested, errors
        )

    except Exception as e:
        logger.exception("[batch-ingest] Failed to ingest results for %s: %s", job_id, e)
        await _mark_ingestion(job_id, IngestionStatus.FAILED)


async def _mark_ingestion(job_id: str, status: IngestionStatus) -> None:
    """Update ingestion_status on the job record."""
    try:
        async with async_session_factory() as db:
            async with db.begin():
                job = (await db.execute(
                    select(BulkScoringJob).where(BulkScoringJob.job_id == job_id)
                )).scalar_one_or_none()
                if job:
                    job.ingestion_status = status
    except Exception as e:
        logger.error("[batch-ingest] Could not update ingestion status for %s: %s", job_id, e)
