"""Alert threshold evaluation — called inline at action points, not as a background job.

Three entry points:
- evaluate_infra_alerts(db)   → called from middleware every N requests
- evaluate_risk_alerts(db)    → called from scoring_complete subscriber
- evaluate_model_alerts(db)   → called from scoring_complete subscriber
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.thresholds import ALERT_THRESHOLDS, check_threshold
from app.models.alert import Alert
from app.models.decision_metric import DecisionMetric
from app.models.model_metric import ModelMetric
from app.models.request_metric import RequestMetric

logger = logging.getLogger(__name__)


async def evaluate_infra_alerts(db: AsyncSession) -> None:
    """Evaluate infrastructure alerts: API latency P99 and error rate.

    Called from middleware every N requests.
    """
    now = datetime.now(timezone.utc)
    window = now - timedelta(hours=1)

    p99 = float((await db.execute(
        select(func.percentile_cont(0.99).within_group(RequestMetric.response_time_ms))
        .where(RequestMetric.timestamp >= window)
    )).scalar() or 0)
    await _evaluate_metric(db, "api_latency_p99", p99, now)

    total = (await db.execute(
        select(func.count(RequestMetric.id)).where(RequestMetric.timestamp >= window)
    )).scalar() or 0

    errors = (await db.execute(
        select(func.count(RequestMetric.id)).where(
            RequestMetric.timestamp >= window,
            RequestMetric.status_code >= 400,
        )
    )).scalar() or 0

    error_rate = (errors / total * 100) if total > 0 else 0.0
    await _evaluate_metric(db, "api_error_rate", error_rate, now)


async def evaluate_risk_alerts(db: AsyncSession) -> None:
    """Evaluate risk + compliance alerts: approval rate shift, data quality, fairness.

    Called from scoring_complete subscriber.
    """
    now = datetime.now(timezone.utc)
    week_ago = now - timedelta(days=7)
    two_weeks_ago = now - timedelta(days=14)
    month_ago = now - timedelta(days=30)

    # 7-day approval rate shift
    current = (await db.execute(
        select(
            func.count(DecisionMetric.id).label("total"),
            func.count(case((DecisionMetric.decision == "APPROVE", 1), else_=None)).label("approves"),
        ).where(DecisionMetric.timestamp >= week_ago)
    )).one()

    prior = (await db.execute(
        select(
            func.count(DecisionMetric.id).label("total"),
            func.count(case((DecisionMetric.decision == "APPROVE", 1), else_=None)).label("approves"),
        ).where(
            DecisionMetric.timestamp >= two_weeks_ago,
            DecisionMetric.timestamp < week_ago,
        )
    )).one()

    current_rate = (current.approves / current.total * 100) if current.total > 0 else 0.0
    prior_rate = (prior.approves / prior.total * 100) if prior.total > 0 else 0.0
    await _evaluate_metric(db, "approval_rate_7d_shift", abs(current_rate - prior_rate), now)

    # Data quality score P25
    dqs_p25 = float((await db.execute(
        select(func.percentile_cont(0.25).within_group(DecisionMetric.data_quality_score))
        .where(
            DecisionMetric.timestamp >= month_ago,
            DecisionMetric.data_quality_score.is_not(None),
        )
    )).scalar() or 1.0)
    await _evaluate_metric(db, "data_quality_score_p25", dqs_p25, now)

    # Fairness max disparity across age groups
    age_groups = [("18-25", 18, 25), ("26-35", 26, 35), ("36-45", 36, 45),
                  ("46-55", 46, 55), ("56+", 56, 200)]
    rates: list[float] = []
    for _label, lo, hi in age_groups:
        age_filter = [
            DecisionMetric.timestamp >= month_ago,
            DecisionMetric.applicant_age >= lo,
            DecisionMetric.applicant_age <= hi,
        ]
        total_age = (await db.execute(
            select(func.count(DecisionMetric.id)).where(*age_filter)
        )).scalar() or 0
        if total_age == 0:
            continue
        approves_age = (await db.execute(
            select(func.count(DecisionMetric.id)).where(
                *age_filter, DecisionMetric.decision == "APPROVE"
            )
        )).scalar() or 0
        rates.append(approves_age / total_age * 100)

    disparity = round(max(rates) - min(rates), 1) if len(rates) >= 2 else 0.0
    await _evaluate_metric(db, "fairness_max_disparity", disparity, now)


async def evaluate_model_alerts(db: AsyncSession) -> None:
    """Evaluate model-ops alerts: score PSI, feature drift PSI, champion AUC drop.

    Called from scoring_complete subscriber after drift metrics are written.
    """
    now = datetime.now(timezone.utc)

    # Score distribution PSI
    psi_record = (await db.execute(
        select(ModelMetric)
        .where(ModelMetric.metric_type == "score_psi")
        .order_by(ModelMetric.timestamp.desc())
        .limit(1)
    )).scalar_one_or_none()
    if psi_record and psi_record.metrics:
        await _evaluate_metric(
            db, "score_distribution_psi",
            float(psi_record.metrics.get("psi", 0.0)), now
        )

    # Feature drift PSI (worst-case feature)
    drift_record = (await db.execute(
        select(ModelMetric)
        .where(ModelMetric.metric_type == "drift")
        .order_by(ModelMetric.timestamp.desc())
        .limit(1)
    )).scalar_one_or_none()
    if drift_record and drift_record.feature_drift:
        worst_feature = max(drift_record.feature_drift, key=lambda k: float(drift_record.feature_drift[k]))
        await _evaluate_metric(
            db, "feature_drift_psi",
            float(drift_record.feature_drift[worst_feature]), now,
            extra={"feature": worst_feature},
        )

    # Champion AUC drop
    auc_record = (await db.execute(
        select(ModelMetric)
        .where(
            ModelMetric.metric_type == "live_performance",
            ModelMetric.model_type == "credit_risk",
        )
        .order_by(ModelMetric.timestamp.desc())
        .limit(1)
    )).scalar_one_or_none()
    if auc_record and auc_record.metrics:
        training_auc = float(auc_record.metrics.get("training_auc", 0))
        live_auc = float(auc_record.metrics.get("live_auc", 0))
        if training_auc > 0:
            drop_pct = abs((live_auc - training_auc) / training_auc * 100)
            await _evaluate_metric(
                db, "champion_auc_drop", drop_pct, now,
                extra={"model_type": "credit_risk"},
            )


# ── Core helper ───────────────────────────────────────────────────────────────

async def _evaluate_metric(
    db: AsyncSession,
    metric_name: str,
    current_value: float,
    now: datetime,
    extra: dict | None = None,
) -> None:
    config = ALERT_THRESHOLDS.get(metric_name)
    if not config:
        return

    status = check_threshold(metric_name, current_value)

    existing = (await db.execute(
        select(Alert).where(Alert.metric == metric_name, Alert.status == "firing")
    )).scalar_one_or_none()

    if status == "firing":
        if existing:
            existing.current_value = current_value
        else:
            alert_id = f"ALT-{now.strftime('%Y%m%d')}-{uuid.uuid4().hex[:6].upper()}"
            db.add(Alert(
                alert_id=alert_id,
                metric=metric_name,
                dashboard=config["dashboard"],
                severity=config["severity"],
                status="firing",
                current_value=current_value,
                threshold=config["threshold"],
                detail=config.get("description", ""),
                recommended_action=config.get("recommended_action", ""),
                feature=(extra or {}).get("feature"),
                model_type=(extra or {}).get("model_type"),
            ))
            logger.warning("[alerts] FIRING: %s = %s (threshold: %s)",
                           metric_name, current_value, config["threshold"])
    elif existing:
        existing.status = "resolved"
        existing.resolved_at = now
        logger.info("[alerts] RESOLVED: %s", metric_name)
