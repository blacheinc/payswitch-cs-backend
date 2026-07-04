"""Alert threshold constants — single source of truth for all dashboards.

Used by:
- Background job `alert_evaluator` to create/resolve alert records.
- Monitoring endpoints to inline alert status in responses.
"""

from __future__ import annotations

from typing import Any

# Each entry defines:
#   threshold  – the numeric boundary
#   op         – comparison operator (">" or "<")
#   severity   – "warning" or "critical"
#   dashboard  – which dashboard owns this metric
ALERT_THRESHOLDS: dict[str, dict[str, Any]] = {
    "api_latency_p99": {
        "threshold": 1000,
        "op": ">",
        "severity": "critical",
        "dashboard": "infrastructure",
        "description": "API p99 latency exceeds 1000 ms",
        "recommended_action": "Investigate slow endpoints, consider auto-scaling",
    },
    "api_error_rate": {
        "threshold": 1.0,
        "op": ">",
        "severity": "critical",
        "dashboard": "infrastructure",
        "description": "API error rate exceeds 1% of requests",
        "recommended_action": "Check error logs, create incident",
    },
    "score_distribution_psi": {
        "threshold": 0.20,
        "op": ">",
        "severity": "critical",
        "dashboard": "model_ops",
        "description": "Score distribution PSI exceeds 0.20",
        "recommended_action": "Trigger retraining pipeline",
    },
    "feature_drift_psi": {
        "threshold": 0.25,
        "op": ">",
        "severity": "warning",
        "dashboard": "model_ops",
        "description": "Feature drift PSI exceeds 0.25",
        "recommended_action": "Review feature engineering pipeline",
    },
    "champion_auc_drop": {
        "threshold": 2.0,
        "op": ">",
        "severity": "critical",
        "dashboard": "model_ops",
        "description": "Champion AUC dropped more than 2%",
        "recommended_action": "Champion-challenger evaluation triggered",
    },
    "approval_rate_7d_shift": {
        "threshold": 10.0,
        "op": ">",
        "severity": "critical",
        "dashboard": "risk",
        "description": "7-day approval rate shifted more than 10% absolute",
        "recommended_action": "Compliance review required",
    },
    "data_quality_score_p25": {
        "threshold": 0.60,
        "op": "<",
        "severity": "warning",
        "dashboard": "compliance",
        "description": "25th percentile data quality score below 0.60",
        "recommended_action": "Data engineering review required",
    },
    "fairness_max_disparity": {
        "threshold": 25.0,
        "op": ">",
        "severity": "critical",
        "dashboard": "compliance",
        "description": "Approval rate disparity between age groups exceeds 25%",
        "recommended_action": "Compliance agent review required",
    },
}


def check_threshold(metric_name: str, current_value: float) -> str:
    """Compare a metric value against its threshold.

    Returns:
        "firing" if threshold is breached, "ok" otherwise.
    """
    config = ALERT_THRESHOLDS.get(metric_name)
    if not config:
        return "ok"

    threshold = config["threshold"]
    op = config["op"]

    if op == ">" and current_value > threshold:
        return "firing"
    if op == "<" and current_value < threshold:
        return "firing"
    return "ok"
