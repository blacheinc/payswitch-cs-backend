"""Subscriber handler for the scoring-complete Service Bus topic.

Processes messages from the AI Engineer's orchestrator when scoring is finished.
Creates a ScoringResult record and updates the ScoreRequest status.

Subscription: backend-sub
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

from app.db import async_session_factory
from app.db.base import utcnow
from app.models.score_request import ScoreRequest, ScoreRequestStatus
from app.models.scoring_result import ScoringDecision, ScoringResult

logger = logging.getLogger(__name__)


async def handle_scoring_complete(message: dict[str, Any]) -> None:

    print(message)

    """Process a scoring-complete message from the AI orchestrator.

    Expected payload:
        request_id, scoring_timestamp, decision, condition_applied,
        credit_risk, fraud_detection, loan_amount, income_verification,
        scoring_metadata, errors (optional)
    """
    request_id = message.get("request_id")
    decision_value = message.get("decision")

    if not request_id:
        logger.error("[scoring-complete] Missing request_id in message")
        return
    if not decision_value:
        logger.error(
            f"[scoring-complete] Missing decision for request {request_id}"
        )
        return

    # Validate decision
    try:
        decision = ScoringDecision(decision_value)
    except ValueError:
        logger.error(
            f"[scoring-complete] Invalid decision '{decision_value}' "
            f"for request {request_id}"
        )
        return

    logger.info(
        f"[scoring-complete] Processing request={request_id} "
        f"decision={decision_value}"
    )

    # Parse scoring timestamp
    scoring_ts = None
    if message.get("scoring_timestamp"):
        try:
            scoring_ts = datetime.fromisoformat(
                message["scoring_timestamp"].replace("Z", "+00:00")
            )
        except (ValueError, TypeError):
            scoring_ts = datetime.now(timezone.utc)

    async with async_session_factory() as db:
        async with db.begin():
            # Check for idempotency — already processed?
            existing = await db.execute(
                select(ScoringResult).where(ScoringResult.request_id == request_id)
            )
            if existing.scalar_one_or_none():
                logger.info(
                    f"[scoring-complete] Already processed request {request_id}, skipping"
                )
                return

            # Find the ScoreRequest
            sr_result = await db.execute(
                select(ScoreRequest).where(ScoreRequest.tracking_id == request_id)
            )
            score_req = sr_result.scalar_one_or_none()

            # Create ScoringResult
            scoring_result = ScoringResult(
                request_id=request_id,
                score_request_id=score_req.id if score_req else None,
                scoring_timestamp=scoring_ts,
                decision=decision,
                condition_applied=message.get("condition_applied", []),
                credit_risk=message.get("credit_risk"),
                fraud_detection=message.get("fraud_detection"),
                loan_amount=message.get("loan_amount"),
                income_verification=message.get("income_verification"),
                scoring_metadata=message.get("scoring_metadata"),
                errors=message.get("errors"),
            )
            db.add(scoring_result)

            # Update ScoreRequest if found
            if score_req:
                if decision == ScoringDecision.ERROR:
                    score_req.status = ScoreRequestStatus.FAILED
                else:
                    score_req.status = ScoreRequestStatus.COMPLETED

                # Denormalize key fields for fast queries
                credit_risk = message.get("credit_risk", {})
                if credit_risk:
                    # pd_value = credit_risk.get("probability_of_default")
                    # if pd_value is not None:
                    #     # Map PD to a 300-850 score for display
                    #     # Lower PD = higher score
                    #     score_req.score_value = int(
                    #         850 - (pd_value * 550)
                    #     )
                    score_req.risk_category = credit_risk.get("risk_tier")
                    score_req.model_version = credit_risk.get("model_version")
                
                score_req.score_value = message.get("scoring_metadata", {}).get("credit_score")

                score_req.confidence = (
                    credit_risk.get("pd_confidence") if credit_risk else None
                )
                score_req.scored_at = scoring_ts or utcnow()
                # We no longer dump the raw JSON here because the API endpoints
                # now natively join the detailed scoring_results table row.

                # Trigger webhook delivery if configured
                try:
                    await _deliver_webhook(score_req, message)
                except Exception as e:
                    logger.warning(
                        f"[scoring-complete] Webhook delivery failed: {e}"
                    )
            else:
                logger.warning(
                    f"[scoring-complete] ScoreRequest not found for {request_id}"
                )

            # ── Inline DecisionMetric for real-time dashboard data ────────
            try:
                from app.models.decision_metric import DecisionMetric

                credit_risk_data = message.get("credit_risk") or {}
                fraud_data = message.get("fraud_detection") or {}
                loan_data = message.get("loan_amount") or {}
                meta = message.get("scoring_metadata") or {}
                shap = credit_risk_data.get("shap_contributions")

                decision_metric = DecisionMetric(
                    scoring_result_id=scoring_result.id,
                    decision=decision_value,
                    credit_score=meta.get("credit_score"),
                    score_grade=meta.get("score_grade"),
                    risk_tier=credit_risk_data.get("risk_tier"),
                    fraud_flag=fraud_data.get("fraud_risk_flag"),
                    loan_tier=loan_data.get("recommended_loan_tier"),
                    probability_of_default=credit_risk_data.get(
                        "probability_of_default"
                    ),
                    data_quality_score=meta.get("data_quality_score"),
                    applicant_age=meta.get("applicant_age_at_application"),
                    has_explainability=bool(shap and len(shap) > 0),
                    timestamp=scoring_ts or utcnow(),
                )
                db.add(decision_metric)
            except Exception as dm_err:
                logger.warning(
                    f"[scoring-complete] Failed to create DecisionMetric: {dm_err}"
                )

    logger.info(
        f"[scoring-complete] Saved ScoringResult for {request_id} "
        f"(decision={decision_value})"
    )

    # ── Inline metrics: drift + alert evaluation ──────────────────────────
    # Runs after the main transaction so failures never affect scoring persistence.
    try:
        from app.db import async_session_factory as _sf
        from app.jobs.drift_calculator import compute_feature_drift, compute_score_psi
        from app.jobs.alert_evaluator import evaluate_risk_alerts, evaluate_model_alerts

        async with _sf() as metrics_db:
            async with metrics_db.begin():
                await compute_score_psi(metrics_db)
                await compute_feature_drift(metrics_db)
                await evaluate_risk_alerts(metrics_db)
                await evaluate_model_alerts(metrics_db)
    except Exception as _me:
        logger.warning("[scoring-complete] Inline metrics evaluation failed: %s", _me)


async def _deliver_webhook(
    score_req: ScoreRequest, scoring_message: dict[str, Any]
) -> None:
    """Trigger webhook delivery for the organization if configured."""
    if not score_req.callback_url:
        return

    try:
        from app.core.events import get_event_publisher

        publisher = get_event_publisher()
        await publisher.publish_webhook_delivery(
            {
                "webhook_url": score_req.callback_url,
                "payload": {
                    "event": "scoring.completed",
                    "request_id": score_req.tracking_id,
                    "decision": scoring_message.get("decision"),
                    "scoring_timestamp": scoring_message.get("scoring_timestamp"),
                    "organization_id": str(score_req.organization_id),
                },
            }
        )
    except Exception as e:
        logger.warning(f"[scoring-complete] Webhook publish failed: {e}")
