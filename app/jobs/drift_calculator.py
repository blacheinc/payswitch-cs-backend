"""Drift utilities — PSI computation for score distributions and SHAP features.

Called inline at action points (scoring_complete subscriber), not as a background job.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import utcnow
from app.models.decision_metric import DecisionMetric
from app.models.model_metric import ModelMetric
from app.models.scoring_result import ScoringResult

logger = logging.getLogger(__name__)

# Training baseline for score distribution (proportions per 100-point bucket).
# [300-400, 400-500, 500-600, 600-700, 700-800, 800-900]
_BASELINE_SCORE_DIST = [0.03, 0.10, 0.25, 0.35, 0.18, 0.09]
_SCORE_BUCKETS = [(300, 400), (400, 500), (500, 600), (600, 700), (700, 800), (800, 900)]

# SHAP value bins for per-feature drift PSI
_SHAP_BINS = [(-999, -0.3), (-0.3, -0.1), (-0.1, 0.0), (0.0, 0.1), (0.1, 0.3), (0.3, 999)]


async def compute_score_psi(db: AsyncSession) -> float | None:
    """Compute score distribution PSI (last 30 days vs baseline) and persist.

    Writes a ModelMetric row with metric_type="score_psi".
    Returns PSI value or None if insufficient data.
    """
    cutoff = datetime.now(UTC) - timedelta(days=30)

    result = await db.execute(
        select(DecisionMetric.credit_score).where(
            DecisionMetric.timestamp >= cutoff,
            DecisionMetric.credit_score.is_not(None),
        )
    )
    scores = [r[0] for r in result]

    if len(scores) < 10:
        logger.debug("[drift] Not enough scores for PSI (%d)", len(scores))
        return None

    current_dist = _score_distribution(scores)
    psi = _psi(_BASELINE_SCORE_DIST, current_dist)

    db.add(
        ModelMetric(
            model_type="credit_risk",
            metric_type="score_psi",
            metrics={"psi": round(psi, 4), "sample_size": len(scores)},
            timestamp=utcnow(),
        )
    )
    logger.info("[drift] Score PSI=%.4f (n=%d)", psi, len(scores))
    return psi


async def compute_feature_drift(db: AsyncSession) -> dict[str, float]:
    """Compute per-feature SHAP drift using a rolling window PSI.

    Compares SHAP value distributions for the last 7 days (current)
    against days 8-37 (baseline window). Writes a ModelMetric row
    with metric_type="drift". Returns {feature_name: psi_value}.
    """
    now = datetime.now(UTC)
    current_start = now - timedelta(days=7)
    baseline_start = now - timedelta(days=37)
    baseline_end = current_start

    def _extract_shap(rows: Sequence[Any]) -> dict[str, list[float]]:
        """Extract {feature: [shap_values]} from ScoringResult rows."""
        by_feature: dict[str, list[float]] = {}
        for (credit_risk,) in rows:
            if not credit_risk:
                continue
            shap_list = credit_risk.get("shap_contributions") or []
            for entry in shap_list:
                if not isinstance(entry, dict):
                    continue
                feat = entry.get("feature")
                # Payload uses "value" or "shap_value"
                val = entry.get("shap_value") if "shap_value" in entry else entry.get("value")
                if feat and val is not None:
                    by_feature.setdefault(feat, []).append(float(val))
        return by_feature

    current_rows = (
        await db.execute(
            select(ScoringResult.credit_risk).where(
                ScoringResult.scoring_timestamp >= current_start,
                ScoringResult.credit_risk.is_not(None),
            )
        )
    ).all()

    baseline_rows = (
        await db.execute(
            select(ScoringResult.credit_risk).where(
                ScoringResult.scoring_timestamp >= baseline_start,
                ScoringResult.scoring_timestamp < baseline_end,
                ScoringResult.credit_risk.is_not(None),
            )
        )
    ).all()

    current_by_feature = _extract_shap(current_rows)
    baseline_by_feature = _extract_shap(baseline_rows)

    feature_psi: dict[str, float] = {}
    for feature, current_vals in current_by_feature.items():
        baseline_vals = baseline_by_feature.get(feature)
        if not baseline_vals or len(current_vals) < 5 or len(baseline_vals) < 5:
            continue
        cur_dist = _shap_distribution(current_vals)
        base_dist = _shap_distribution(baseline_vals)
        feature_psi[feature] = round(_psi(base_dist, cur_dist), 4)

    db.add(
        ModelMetric(
            model_type="credit_risk",
            metric_type="drift",
            feature_drift=feature_psi,
            timestamp=utcnow(),
        )
    )

    if feature_psi:
        worst = max(feature_psi, key=lambda k: feature_psi[k])
        logger.info("[drift] Feature drift: worst=%s PSI=%.4f", worst, feature_psi[worst])
    else:
        logger.debug("[drift] No feature drift data (insufficient SHAP history)")

    return feature_psi


# ── Pure utility functions ────────────────────────────────────────────────────


def _score_distribution(scores: list[int]) -> list[float]:
    n = len(scores)
    return [sum(1 for s in scores if lo <= s < hi) / n for lo, hi in _SCORE_BUCKETS]


def _shap_distribution(values: list[float]) -> list[float]:
    n = len(values)
    return [sum(1 for v in values if lo <= v < hi) / n for lo, hi in _SHAP_BINS]


def _psi(expected: list[float], actual: list[float]) -> float:
    """Population Stability Index. <0.10 stable, 0.10-0.20 moderate, ≥0.20 drift."""
    eps = 1e-6
    return abs(
        sum(
            (max(a, eps) - max(e, eps)) * math.log(max(a, eps) / max(e, eps))
            for e, a in zip(expected, actual)
        )
    )
