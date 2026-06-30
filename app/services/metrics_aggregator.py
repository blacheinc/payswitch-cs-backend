"""Metrics aggregator — query helpers for all monitoring dashboard endpoints.

Reads from:
- request_metrics (or api_logs) for infrastructure dashboard
- decision_metrics for risk and compliance dashboards
- model_metrics for model-ops dashboard
- alerts table for unified alert feed

All functions accept a DB session and return dicts matching the response schemas.
"""

from __future__ import annotations

import logging
import statistics
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import Float, Integer, case, cast, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.thresholds import ALERT_THRESHOLDS, check_threshold
from app.models.alert import Alert
from app.models.decision_metric import DecisionMetric
from app.models.model_metric import ModelMetric
from app.models.request_metric import RequestMetric

logger = logging.getLogger(__name__)


# ── Period helpers ────────────────────────────────────────────────────────────

PERIOD_DELTA = {
    "1h": timedelta(hours=1),
    "6h": timedelta(hours=6),
    "24h": timedelta(hours=24),
    "7d": timedelta(days=7),
    "30d": timedelta(days=30),
    "90d": timedelta(days=90),
}

# Bucket sizes for timeseries (in minutes)
BUCKET_MINUTES = {
    "1h": 1,
    "6h": 5,
    "24h": 15,
    "7d": 60,
    "30d": 360,
    "90d": 1440,
}


def _period_start(period: str) -> datetime:
    """Compute the start timestamp for a given period string."""
    delta = PERIOD_DELTA.get(period, timedelta(hours=24))
    return datetime.now(UTC) - delta


def _now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _safe_pct(numerator: int, denominator: int) -> float:
    """Safe percentage calculation."""
    if denominator == 0:
        return 0.0
    return round(numerator / denominator * 100, 1)


# ── Infrastructure Metrics ────────────────────────────────────────────────────


async def get_infrastructure_metrics(
    db: AsyncSession,
    period: str = "24h",
    endpoint_filter: str | None = None,
) -> dict[str, Any]:
    """Aggregate request metrics for the infrastructure dashboard."""
    start = _period_start(period)
    bucket_min = BUCKET_MINUTES.get(period, 15)

    # Base query filter
    base_filter = [RequestMetric.timestamp >= start]
    if endpoint_filter:
        base_filter.append(RequestMetric.endpoint == endpoint_filter)

    # ── Total request volume ──────────────────────────────────────────────
    total_q = select(func.count(RequestMetric.id)).where(*base_filter)
    total_result = await db.execute(total_q)
    total_count = total_result.scalar() or 0

    # ── Volume by endpoint ────────────────────────────────────────────────
    vol_q = (
        select(
            RequestMetric.endpoint,
            RequestMetric.method,
            func.count(RequestMetric.id).label("count"),
        )
        .where(*base_filter)
        .group_by(RequestMetric.endpoint, RequestMetric.method)
        .order_by(func.count(RequestMetric.id).desc())
        .limit(20)
    )
    vol_result = await db.execute(vol_q)
    by_endpoint = [
        {"endpoint": r.endpoint, "method": r.method, "count": r.count} for r in vol_result
    ]

    # ── Overall latency percentiles ───────────────────────────────────────
    latency_q = (
        select(RequestMetric.response_time_ms)
        .where(*base_filter)
        .order_by(RequestMetric.response_time_ms)
    )
    latency_result = await db.execute(latency_q)
    all_latencies = [r[0] for r in latency_result]

    p50, p95, p99 = _compute_percentiles(all_latencies)

    # ── Latency by endpoint ───────────────────────────────────────────────
    lat_by_ep_q = (
        select(
            RequestMetric.endpoint,
            func.percentile_cont(0.5).within_group(RequestMetric.response_time_ms).label("p50"),
            func.percentile_cont(0.95).within_group(RequestMetric.response_time_ms).label("p95"),
            func.percentile_cont(0.99).within_group(RequestMetric.response_time_ms).label("p99"),
        )
        .where(*base_filter)
        .group_by(RequestMetric.endpoint)
        .limit(20)
    )
    lat_by_ep_result = await db.execute(lat_by_ep_q)
    latency_by_endpoint = [
        {
            "endpoint": r.endpoint,
            "p50_ms": round(r.p50 or 0, 1),
            "p95_ms": round(r.p95 or 0, 1),
            "p99_ms": round(r.p99 or 0, 1),
        }
        for r in lat_by_ep_result
    ]

    # ── Error rates ───────────────────────────────────────────────────────
    error_filter = base_filter + [RequestMetric.status_code >= 400]
    error_count_q = select(func.count(RequestMetric.id)).where(*error_filter)
    error_count = (await db.execute(error_count_q)).scalar() or 0
    overall_error_pct = _safe_pct(error_count, total_count)

    error_by_status_q = (
        select(
            RequestMetric.status_code,
            func.count(RequestMetric.id).label("count"),
        )
        .where(*error_filter)
        .group_by(RequestMetric.status_code)
        .order_by(RequestMetric.status_code)
    )
    error_by_status = await db.execute(error_by_status_q)
    by_status = [
        {
            "status_code": status_code,
            "count": count,
            "pct": _safe_pct(count, total_count),
        }
        for status_code, count in error_by_status
    ]

    # ── Timeseries ────────────────────────────────────────────────────────
    timeseries = await _build_infra_timeseries(db, start, bucket_min, base_filter)

    # ── Alerts ────────────────────────────────────────────────────────────
    alerts = [
        {
            "metric": "api_latency_p99",
            "current_value": p99,
            "threshold": ALERT_THRESHOLDS["api_latency_p99"]["threshold"],
            "status": check_threshold("api_latency_p99", p99),
        },
        {
            "metric": "api_error_rate",
            "current_value": overall_error_pct,
            "threshold": ALERT_THRESHOLDS["api_error_rate"]["threshold"],
            "status": check_threshold("api_error_rate", overall_error_pct),
        },
    ]

    return {
        "period": period,
        "generated_at": _now_iso(),
        "request_volume": {"total": total_count, "by_endpoint": by_endpoint},
        "latency": {
            "p50_ms": p50,
            "p95_ms": p95,
            "p99_ms": p99,
            "by_endpoint": latency_by_endpoint,
        },
        "error_rates": {"overall_pct": overall_error_pct, "by_status": by_status},
        "timeseries": timeseries,
        "alerts": alerts,
    }


async def _build_infra_timeseries(
    db: AsyncSession,
    start: datetime,
    bucket_min: int,
    base_filter: list,
) -> list[dict]:
    """Build timeseries buckets for infrastructure metrics.

    Floors each timestamp to the nearest bucket_min-minute boundary using
    epoch arithmetic so bucket size correctly reflects the chosen period.
    """
    bucket_seconds = bucket_min * 60

    # Floor to bucket boundary: to_timestamp(floor(epoch / bucket_s) * bucket_s)
    epoch_expr = func.extract("epoch", RequestMetric.timestamp).cast(Integer)
    bucket_expr = func.to_timestamp((epoch_expr / bucket_seconds) * bucket_seconds).label("bucket")

    ts_q = (
        select(
            bucket_expr,
            func.count(RequestMetric.id).label("requests"),
            func.count(
                case(
                    (RequestMetric.status_code >= 400, 1),
                    else_=None,
                )
            ).label("errors"),
            func.percentile_cont(0.99).within_group(RequestMetric.response_time_ms).label("p99"),
        )
        .where(*base_filter)
        .group_by(text("bucket"))
        .order_by(text("bucket"))
    )
    ts_result = await db.execute(ts_q)
    return [
        {
            "bucket": r.bucket.isoformat().replace("+00:00", "Z") if r.bucket else "",
            "requests": r.requests,
            "errors": r.errors,
            "p99_ms": round(r.p99 or 0, 1),
        }
        for r in ts_result
    ]


def _compute_percentiles(values: list[int]) -> tuple[float, float, float]:
    """Compute p50, p95, p99 from a sorted list of values."""
    if not values:
        return 0.0, 0.0, 0.0
    n = len(values)
    p50 = float(values[int(n * 0.50)])
    p95 = float(values[min(int(n * 0.95), n - 1)])
    p99 = float(values[min(int(n * 0.99), n - 1)])
    return round(p50, 1), round(p95, 1), round(p99, 1)


# ── Risk Metrics ──────────────────────────────────────────────────────────────


async def get_risk_metrics(
    db: AsyncSession,
    period: str = "7d",
    segment_filter: str | None = None,
) -> dict[str, Any]:
    """Aggregate decision metrics for the risk dashboard."""
    start = _period_start(period)

    base_filter = [DecisionMetric.timestamp >= start]
    if segment_filter:
        base_filter.append(DecisionMetric.score_grade == segment_filter)

    # ── Total decisions ───────────────────────────────────────────────────
    total_q = select(func.count(DecisionMetric.id)).where(*base_filter)
    total = (await db.execute(total_q)).scalar() or 0

    # ── By decision ───────────────────────────────────────────────────────
    decision_q = (
        select(
            DecisionMetric.decision,
            func.count(DecisionMetric.id).label("count"),
        )
        .where(*base_filter)
        .group_by(DecisionMetric.decision)
    )
    decision_result = await db.execute(decision_q)
    decision_counts = {decision: count for decision, count in decision_result}

    approve = decision_counts.get("APPROVE", 0)
    conditional = decision_counts.get("CONDITIONAL_APPROVE", 0)
    decline = decision_counts.get("DECLINE", 0)
    refer = decision_counts.get("REFER", 0)
    error = decision_counts.get("ERROR", 0)

    overall = {
        "total_decisions": total,
        "approve": approve,
        "conditional_approve": conditional,
        "decline": decline,
        "refer": refer,
        "error": error,
        "approve_rate_pct": _safe_pct(approve, total),
        "conditional_approve_rate_pct": _safe_pct(conditional, total),
        "decline_rate_pct": _safe_pct(decline, total),
    }

    # ── By grade ──────────────────────────────────────────────────────────
    grade_q = (
        select(
            DecisionMetric.score_grade,
            func.count(DecisionMetric.id).label("total"),
            func.count(case((DecisionMetric.decision == "APPROVE", 1), else_=None)).label(
                "approve_count"
            ),
            func.count(case((DecisionMetric.decision == "DECLINE", 1), else_=None)).label(
                "decline_count"
            ),
        )
        .where(*base_filter)
        .where(DecisionMetric.score_grade.is_not(None))
        .group_by(DecisionMetric.score_grade)
        .order_by(DecisionMetric.score_grade)
    )
    grade_result = await db.execute(grade_q)
    by_grade = [
        {
            "grade": grade,
            "total": grade_total,
            "approve_rate_pct": _safe_pct(approve_count, grade_total),
            "decline_rate_pct": _safe_pct(decline_count, grade_total),
        }
        for grade, grade_total, approve_count, decline_count in grade_result
    ]

    # ── Score distribution ────────────────────────────────────────────────
    score_q = (
        select(DecisionMetric.credit_score)
        .where(*base_filter)
        .where(DecisionMetric.credit_score.is_not(None))
    )
    score_result = await db.execute(score_q)
    scores = [r[0] for r in score_result]

    score_distribution = _build_score_distribution(scores)

    # ── Risk tier breakdown ───────────────────────────────────────────────
    tier_q = (
        select(
            DecisionMetric.risk_tier,
            func.count(DecisionMetric.id).label("count"),
        )
        .where(*base_filter)
        .where(DecisionMetric.risk_tier.is_not(None))
        .group_by(DecisionMetric.risk_tier)
    )
    tier_result = await db.execute(tier_q)
    tier_data = [(tier, count) for tier, count in tier_result]
    tier_total = sum(c for _, c in tier_data)
    risk_tiers = [{"tier": t, "count": c, "pct": _safe_pct(c, tier_total)} for t, c in tier_data]

    # ── Timeseries ────────────────────────────────────────────────────────
    ts_q = (
        select(
            func.date_trunc("day", DecisionMetric.timestamp).label("bucket"),
            func.count(DecisionMetric.id).label("total_decisions"),
            func.count(case((DecisionMetric.decision == "APPROVE", 1), else_=None)).label(
                "approve_count"
            ),
            func.count(case((DecisionMetric.decision == "DECLINE", 1), else_=None)).label(
                "decline_count"
            ),
            func.avg(cast(DecisionMetric.credit_score, Float)).label("mean_score"),
        )
        .where(*base_filter)
        .group_by("bucket")
        .order_by("bucket")
    )
    ts_result = await db.execute(ts_q)
    timeseries = [
        {
            "bucket": bucket.strftime("%Y-%m-%d") if bucket else "",
            "total_decisions": total_decisions,
            "approve_rate_pct": _safe_pct(approve_count, total_decisions),
            "decline_rate_pct": _safe_pct(decline_count, total_decisions),
            "mean_credit_score": round(mean_score or 0, 0),
        }
        for bucket, total_decisions, approve_count, decline_count, mean_score in ts_result
    ]

    # ── 7-day shift alert ─────────────────────────────────────────────────
    current_approve_rate = _safe_pct(approve, total)
    prior_start = start - PERIOD_DELTA.get(period, timedelta(days=7))
    prior_q = select(
        func.count(DecisionMetric.id).label("total"),
        func.count(case((DecisionMetric.decision == "APPROVE", 1), else_=None)).label(
            "approve_count"
        ),
    ).where(
        DecisionMetric.timestamp >= prior_start,
        DecisionMetric.timestamp < start,
    )
    prior_result = await db.execute(prior_q)
    prior_total, prior_approve_count = prior_result.one()
    prior_rate = _safe_pct(prior_approve_count, prior_total)
    shift = abs(current_approve_rate - prior_rate)

    alerts = [
        {
            "metric": "approval_rate_7d_shift",
            "current_value": round(shift, 1),
            "threshold": ALERT_THRESHOLDS["approval_rate_7d_shift"]["threshold"],
            "status": check_threshold("approval_rate_7d_shift", shift),
        },
    ]

    return {
        "period": period,
        "generated_at": _now_iso(),
        "approval_rates": {"overall": overall, "by_grade": by_grade},
        "score_distribution": score_distribution,
        "risk_tier_breakdown": risk_tiers,
        "timeseries": timeseries,
        "alerts": alerts,
    }


def _build_score_distribution(scores: list[int]) -> dict[str, Any]:
    """Build histogram buckets and stats from a list of credit scores."""
    if not scores:
        return {
            "buckets": [],
            "mean": 0.0,
            "median": 0.0,
            "std_dev": 0.0,
        }

    # 100-point buckets from 300 to 900
    bucket_ranges = [(s, s + 100) for s in range(300, 900, 100)]
    buckets = []
    for lo, hi in bucket_ranges:
        count = sum(1 for s in scores if lo <= s < hi)
        buckets.append({"range": f"{lo}-{hi}", "count": count})

    return {
        "buckets": buckets,
        "mean": round(statistics.mean(scores), 0),
        "median": round(statistics.median(scores), 0),
        "std_dev": round(statistics.stdev(scores) if len(scores) > 1 else 0.0, 0),
    }


# ── Model Ops Metrics ─────────────────────────────────────────────────────────


async def get_model_ops_metrics(
    db: AsyncSession,
    period: str = "30d",
    model_type_filter: str | None = None,
) -> dict[str, Any]:
    """Aggregate model ops metrics for the model-ops dashboard.

    Champion data comes from the blob (passed in by the router), drift and
    retraining data come from the model_metrics table.
    """
    start = _period_start(period)

    base_filter = [ModelMetric.timestamp >= start]
    if model_type_filter:
        base_filter.append(ModelMetric.model_type == model_type_filter)

    # ── Feature drift ─────────────────────────────────────────────────────
    drift_q = (
        select(ModelMetric)
        .where(*base_filter, ModelMetric.metric_type == "drift")
        .order_by(ModelMetric.timestamp.desc())
        .limit(1)
    )
    drift_result = await db.execute(drift_q)
    drift_record = drift_result.scalar_one_or_none()

    feature_drift = []
    if drift_record and drift_record.feature_drift:
        for feature_name, psi_value in drift_record.feature_drift.items():
            psi = float(psi_value) if psi_value else 0.0
            status = "alert" if psi > ALERT_THRESHOLDS["feature_drift_psi"]["threshold"] else "ok"
            feature_drift.append({"feature": feature_name, "psi": psi, "status": status})

    # ── Retraining history ────────────────────────────────────────────────
    retrain_q = (
        select(ModelMetric)
        .where(*base_filter, ModelMetric.metric_type == "training")
        .order_by(ModelMetric.timestamp.desc())
        .limit(20)
    )
    retrain_result = await db.execute(retrain_q)
    retraining_history = []
    for r in retrain_result.scalars():
        metrics = r.metrics or {}
        retraining_history.append(
            {
                "training_id": r.training_id or "",
                "completed_at": r.timestamp.isoformat().replace("+00:00", "Z")
                if r.timestamp
                else "",
                "model_type": r.model_type,
                "result": metrics.get("result", "unknown"),
                "metrics_before": metrics.get("metrics_before", {}),
                "metrics_after": metrics.get("metrics_after", {}),
            }
        )

    # ── Score distribution PSI ────────────────────────────────────────────
    psi_q = (
        select(ModelMetric)
        .where(*base_filter, ModelMetric.metric_type == "score_psi")
        .order_by(ModelMetric.timestamp.desc())
        .limit(10)
    )
    psi_result = await db.execute(psi_q)
    score_psi = []
    for r in psi_result.scalars():
        metrics = r.metrics or {}
        psi_val = metrics.get("psi", 0.0)
        status = (
            "alert" if psi_val > ALERT_THRESHOLDS["score_distribution_psi"]["threshold"] else "ok"
        )
        score_psi.append({"model_type": r.model_type, "psi": psi_val, "status": status})

    # ── Alerts ────────────────────────────────────────────────────────────
    alerts = []
    # Feature drift alerts
    for fd in feature_drift:
        if fd["status"] == "alert":
            alerts.append(
                {
                    "metric": "feature_drift_psi",
                    "feature": fd["feature"],
                    "current_value": fd["psi"],
                    "threshold": ALERT_THRESHOLDS["feature_drift_psi"]["threshold"],
                    "status": "firing",
                }
            )

    # Score distribution alerts
    for sp in score_psi:
        if sp["status"] == "alert":
            alerts.append(
                {
                    "metric": "score_distribution_psi",
                    "model_type": sp["model_type"],
                    "current_value": sp["psi"],
                    "threshold": ALERT_THRESHOLDS["score_distribution_psi"]["threshold"],
                    "status": "firing",
                }
            )

    return {
        "period": period,
        "generated_at": _now_iso(),
        "feature_drift": feature_drift,
        "retraining_history": retraining_history,
        "score_distribution_psi": score_psi,
        "alerts": alerts,
    }


# ── Compliance Metrics ────────────────────────────────────────────────────────


async def get_compliance_metrics(
    db: AsyncSession,
    period: str = "30d",
) -> dict[str, Any]:
    """Aggregate compliance metrics."""
    start = _period_start(period)
    base_filter = [DecisionMetric.timestamp >= start]

    # ── Fairness: approval rates by age group ─────────────────────────────
    age_groups = [
        ("18-25", 18, 25),
        ("26-35", 26, 35),
        ("36-45", 36, 45),
        ("46-55", 46, 55),
        ("56+", 56, 200),
    ]

    age_data = []
    rates_with_data: list[float] = []
    for label, lo, hi in age_groups:
        age_filter = base_filter + [
            DecisionMetric.applicant_age >= lo,
            DecisionMetric.applicant_age <= hi,
        ]
        total_q = select(func.count(DecisionMetric.id)).where(*age_filter)
        total = (await db.execute(total_q)).scalar() or 0

        approve_q = select(func.count(DecisionMetric.id)).where(
            *age_filter, DecisionMetric.decision == "APPROVE"
        )
        approves = (await db.execute(approve_q)).scalar() or 0

        rate = _safe_pct(approves, total)
        age_data.append({"group": label, "approval_rate_pct": rate, "count": total})
        if total > 0:
            rates_with_data.append(rate)

    # Max disparity between any two groups (with non-zero counts)
    max_disparity = 0.0
    if len(rates_with_data) >= 2:
        max_disparity = round(max(rates_with_data) - min(rates_with_data), 1)

    # ── Audit log coverage ────────────────────────────────────────────────
    total_decisions_q = select(func.count(DecisionMetric.id)).where(*base_filter)
    total_decisions = (await db.execute(total_decisions_q)).scalar() or 0

    explainability_q = select(func.count(DecisionMetric.id)).where(
        *base_filter, DecisionMetric.has_explainability.is_(True)
    )
    with_explain = (await db.execute(explainability_q)).scalar() or 0

    error_q = select(func.count(DecisionMetric.id)).where(
        *base_filter, DecisionMetric.decision == "ERROR"
    )
    error_count = (await db.execute(error_q)).scalar() or 0

    # Average data quality score
    dqs_q = select(
        func.avg(DecisionMetric.data_quality_score),
        func.percentile_cont(0.25).within_group(DecisionMetric.data_quality_score),
    ).where(*base_filter, DecisionMetric.data_quality_score.is_not(None))
    dqs_result = await db.execute(dqs_q)
    dqs_row = dqs_result.one()
    avg_dqs = round(float(dqs_row[0] or 0), 2)
    dqs_p25 = round(float(dqs_row[1] or 0), 2)

    # ── Alerts ────────────────────────────────────────────────────────────
    alerts = [
        {
            "metric": "data_quality_score_p25",
            "current_value": dqs_p25,
            "threshold": ALERT_THRESHOLDS["data_quality_score_p25"]["threshold"],
            "status": check_threshold("data_quality_score_p25", dqs_p25),
        },
        {
            "metric": "fairness_max_disparity",
            "current_value": max_disparity,
            "threshold": ALERT_THRESHOLDS["fairness_max_disparity"]["threshold"],
            "status": check_threshold("fairness_max_disparity", max_disparity),
        },
    ]

    return {
        "period": period,
        "generated_at": _now_iso(),
        "fairness_metrics": {
            "demographic_parity": {
                "approval_rate_by_age_group": age_data,
                "max_disparity_pct": max_disparity,
            },
        },
        "audit_log": {
            "total_decisions_logged": total_decisions,
            "decisions_with_full_explainability": with_explain,
            "coverage_pct": _safe_pct(with_explain, total_decisions),
            "error_decisions": error_count,
            "avg_data_quality_score": avg_dqs,
            "dqs_p25": dqs_p25,
        },
        "data_subject_requests": {
            "total": 0,
            "pending": 0,
            "completed": 0,
            "avg_resolution_days": 0.0,
        },
        "regulation_alerts": [],
        "alerts": alerts,
    }


# ── Alerts ────────────────────────────────────────────────────────────────────


async def get_alerts(
    db: AsyncSession,
    status_filter: str | None = None,
    severity_filter: str | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    """Query the alerts table for the unified alert feed."""
    filters = []
    if status_filter:
        filters.append(Alert.status == status_filter)
    if severity_filter:
        filters.append(Alert.severity == severity_filter)

    q = select(Alert).where(*filters).order_by(Alert.created_at.desc()).limit(limit)
    result = await db.execute(q)
    alert_rows = result.scalars().all()

    alerts = [
        {
            "id": a.alert_id,
            "metric": a.metric,
            "dashboard": a.dashboard,
            "severity": a.severity,
            "status": a.status,
            "current_value": a.current_value,
            "threshold": a.threshold,
            "detail": a.detail,
            "feature": a.feature,
            "model_type": a.model_type,
            "recommended_action": a.recommended_action,
            "created_at": a.created_at.isoformat().replace("+00:00", "Z") if a.created_at else "",
            "resolved_at": (
                a.resolved_at.isoformat().replace("+00:00", "Z") if a.resolved_at else None
            ),
        }
        for a in alert_rows
    ]

    # Summary
    firing = [a for a in alert_rows if a.status == "firing"]
    summary = {
        "total_firing": len(firing),
        "critical_firing": sum(1 for a in firing if a.severity == "critical"),
        "warning_firing": sum(1 for a in firing if a.severity == "warning"),
    }

    return {
        "generated_at": _now_iso(),
        "alerts": alerts,
        "summary": summary,
    }
