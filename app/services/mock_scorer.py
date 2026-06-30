"""Mock scoring engine for MVP development.

This module simulates a credit scoring model. In production, this will be
replaced by calls to the AI team's model-serving microservice.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

from app.schemas.score_request import (
    AffordabilityAssessment,
    ModelInfo,
    Recommendation,
    RiskFactor,
    ScoreComponent,
    ScoreComponents,
    ScoreRequestResponse,
    ScoreTimestamps,
    ScoreValue,
)


def _deterministic_score(payload: dict[str, Any]) -> int:
    """Generate a deterministic but realistic score from the input hash.

    This ensures the same input always produces the same score,
    which is important for idempotency and testing.
    """
    # Hash the full name + national ID for determinism
    applicant = payload.get("applicant", {})
    seed = f"{applicant.get('full_name', '')}-{applicant.get('national_id_number', '')}"
    hash_int = int(hashlib.sha256(seed.encode()).hexdigest()[:8], 16)
    # Map to 300-850 range
    return 300 + (hash_int % 551)


def _risk_category(score: int) -> tuple[str, str]:
    """Map score to risk category."""
    if score >= 750:
        return "very_low", "Excellent credit profile with minimal risk indicators"
    elif score >= 650:
        return "low", "Good credit profile with few risk concerns"
    elif score >= 550:
        return "medium", "Moderate credit profile with some risk factors"
    elif score >= 450:
        return "high", "Elevated risk profile requiring careful evaluation"
    else:
        return "very_high", "High risk profile with significant concerns"


def generate_mock_score(
    tracking_id: str,
    request_payload: dict[str, Any],
    reference_id: str | None = None,
) -> ScoreRequestResponse:
    """Generate a complete mock score response matching the PRD schema."""
    score_value = _deterministic_score(request_payload)
    risk_cat, risk_desc = _risk_category(score_value)
    now = datetime.now(UTC)

    # Generate score components
    base = score_value / 850
    components = ScoreComponents(
        payment_history=ScoreComponent(
            score=int(base * 200),
            max_score=200,
            weight=0.35,
            grade="A" if base > 0.7 else "B",
        ),
        credit_utilization=ScoreComponent(
            score=int(base * 150),
            max_score=150,
            weight=0.30,
            grade="A" if base > 0.75 else "B",
        ),
        income_stability=ScoreComponent(
            score=int(base * 120), max_score=120, weight=0.15, grade="B"
        ),
        credit_history_length=ScoreComponent(
            score=int(base * 100), max_score=100, weight=0.10, grade="B"
        ),
        debt_burden=ScoreComponent(
            score=int(base * 80),
            max_score=80,
            weight=0.10,
            grade="B" if base > 0.5 else "C",
        ),
    )

    # Risk factors based on score
    risk_factors = []
    if score_value < 650:
        risk_factors.append(
            RiskFactor(
                code="RF001",
                category="payment_history",
                description="Limited payment history available",
                impact="negative",
                severity="medium",
            )
        )
    if score_value < 500:
        risk_factors.append(
            RiskFactor(
                code="RF002",
                category="debt_burden",
                description="High debt-to-income ratio",
                impact="negative",
                severity="high",
            )
        )

    # Recommendations
    recommendations = []
    if score_value >= 650:
        recommendations.append(
            Recommendation(
                type="approval",
                message="Applicant meets standard underwriting criteria",
                priority="high",
            )
        )
    elif score_value >= 450:
        recommendations.append(
            Recommendation(
                type="review",
                message="Manual review recommended before decision",
                priority="medium",
            )
        )
    else:
        recommendations.append(
            Recommendation(
                type="decline",
                message="Risk profile exceeds acceptable threshold",
                priority="high",
            )
        )

    # Affordability
    loan_request = request_payload.get("loan_request", {})
    loan_amount = loan_request.get("amount", 10000)
    tenure_months = loan_request.get("tenure_months", 12)
    monthly_income = (request_payload.get("employment") or {}).get("monthly_income", 0) or 5000
    monthly_payment = loan_amount / max(tenure_months, 1) * 1.15  # Rough estimate

    affordability = AffordabilityAssessment(
        estimated_monthly_payment=round(monthly_payment, 2),
        debt_to_income_current=round(secrets.SystemRandom().uniform(0.1, 0.4), 4),
        debt_to_income_projected=round(monthly_payment / max(monthly_income, 1), 4),
        disposable_income=round(monthly_income * 0.6, 2),
        affordability_assessment=(
            "affordable" if monthly_payment < monthly_income * 0.4 else "stretched"
        ),
        max_recommended_amount=round(monthly_income * 0.4 * tenure_months / 1.15, 2),
    )

    return ScoreRequestResponse(
        request_id=tracking_id,
        reference_id=reference_id,
        status="completed",
        score=ScoreValue(
            value=score_value,
            percentile=int(base * 100),
            risk_category=risk_cat,
            risk_category_description=risk_desc,
            confidence=round(0.7 + base * 0.25, 4),
            data_quality_flag="sufficient",
        ),
        score_components=components,
        risk_factors=risk_factors or None,
        recommendations=recommendations,
        affordability=affordability,
        model_info=ModelInfo(
            model_version="mock-v1.0.0",
            model_type="gradient_boosting_ensemble",
            features_used=42,
            data_sources=["application", "bureau", "alternative"],
        ),
        timestamps=ScoreTimestamps(
            requested_at=now,
            scored_at=now,
            valid_until=now + timedelta(days=14),
        ),
    )
