"""GET /v1/monitoring/model-ops — champion performance, feature drift, retraining."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.thresholds import ALERT_THRESHOLDS
from app.db import get_db
from app.dependencies import AuthContext, require_permission
from app.services.metrics_aggregator import get_model_ops_metrics

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/model-ops")
async def model_ops_dashboard(
    _auth: Annotated[AuthContext, Depends(require_permission("monitoring.model_ops"))],
    db: Annotated[AsyncSession, Depends(get_db)],
    model_type: str | None = Query(
        None,
        pattern="^(credit_risk|fraud_detection|loan_amount|income_verification)$",
        description="Filter by model type",
    ),
    period: str = Query("30d", pattern="^(7d|30d|90d)$"),
):
    """ModelOps Dashboard — champion model performance, feature drift, retraining history.

    Query params:
    - model_type: credit_risk, fraud_detection, loan_amount, income_verification
    - period: 7d, 30d, 90d (default: 30d)
    """
    # Get drift/retraining data from DB
    ops_data = await get_model_ops_metrics(db, period=period, model_type_filter=model_type)

    # Get champion data from blob
    champions = []
    try:
        from sqlalchemy import select

        from app.models.model_version import ModelVersion
        from app.storage.champion_blob import read_champion_metadata

        blob_data = read_champion_metadata()
        if blob_data and "models" in blob_data:
            # Build a (model_type, version) → promoted_at lookup up front so
            # we don't fire one query per champion. The ML team's blob
            # doesn't include a promoted_at timestamp today, but the
            # ModelVersion table does — the FE's overview dashboard wants
            # it to distinguish "deployed" from "promoted".
            type_versions = [
                (m.get("model_type", ""), str(m.get("version", ""))) for m in blob_data["models"]
            ]
            promoted_lookup: dict[tuple[str, str], datetime] = {}
            if type_versions:
                rows = (
                    await db.execute(
                        select(
                            ModelVersion.model_type,
                            ModelVersion.version,
                            ModelVersion.promoted_at,
                        ).where(ModelVersion.promoted_at.is_not(None))
                    )
                ).all()
                for mt, ver, promoted in rows:
                    promoted_lookup[(mt, str(ver))] = promoted

            for model in blob_data["models"]:
                mt = model.get("model_type", "")
                if model_type and mt != model_type:
                    continue

                current_metrics = model.get("metrics", {})
                training_auc = current_metrics.get("auc")

                # Check for live AUC from model_metrics (if available)
                live_auc = None
                auc_change_pct = None
                auc_alert = None
                if training_auc is not None:
                    # TODO: compute live_auc from feedback data in model_metrics
                    live_auc = training_auc  # placeholder until feedback loop exists
                    auc_change_pct = 0.0
                    auc_alert = False

                version = str(model.get("version", ""))
                promoted_at = promoted_lookup.get((mt, version))

                champions.append(
                    {
                        "model_type": mt,
                        "registry_name": model.get("registry_name", ""),
                        "version": version,
                        "status": model.get("status", ""),
                        "created_at": model.get("created_at"),
                        "promoted_at": (
                            promoted_at.isoformat().replace("+00:00", "Z") if promoted_at else None
                        ),
                        "current_metrics": current_metrics,
                        "live_auc": live_auc,
                        "auc_change_pct": auc_change_pct,
                        "auc_alert": auc_alert,
                    }
                )
    except Exception as e:
        logger.warning(f"[model-ops] Failed to read champion blob: {e}")

    # Fallback: when the champion blob is unavailable (e.g. object storage not
    # configured), build the champions list from the model_versions table so
    # the dashboard still renders. A champion is the most recently promoted (or
    # production) version per model_type.
    if not champions:
        try:
            from sqlalchemy import select

            from app.models.model_version import ModelStatus, ModelVersion

            fallback_rows = (
                (
                    await db.execute(
                        select(ModelVersion)
                        .where(ModelVersion.status == ModelStatus.PRODUCTION)
                        .order_by(ModelVersion.promoted_at.desc().nullslast())
                    )
                )
                .scalars()
                .all()
            )

            seen: set[str] = set()
            for mv in fallback_rows:
                if model_type and mv.model_type != model_type:
                    continue
                if mv.model_type in seen:
                    continue
                seen.add(mv.model_type)

                current_metrics = mv.metrics or {}
                training_auc = current_metrics.get("auc")
                live_auc = training_auc if training_auc is not None else None

                champions.append(
                    {
                        "model_type": mv.model_type,
                        "registry_name": f"{mv.model_type}_model",
                        "version": str(mv.version),
                        "status": (mv.status.value if hasattr(mv.status, "value") else mv.status),
                        "created_at": (
                            mv.created_at.isoformat().replace("+00:00", "Z")
                            if mv.created_at
                            else None
                        ),
                        "promoted_at": (
                            mv.promoted_at.isoformat().replace("+00:00", "Z")
                            if mv.promoted_at
                            else None
                        ),
                        "current_metrics": current_metrics,
                        "live_auc": live_auc,
                        "auc_change_pct": 0.0 if live_auc is not None else None,
                        "auc_alert": False,
                    }
                )
        except Exception as e:
            logger.warning(f"[model-ops] DB champion fallback failed: {e}")

    ops_data["champions"] = champions

    # Add AUC drop alert if applicable
    for champ in champions:
        if champ.get("auc_change_pct") is not None and champ["auc_change_pct"] is not None:
            drop = abs(champ["auc_change_pct"])
            if drop > ALERT_THRESHOLDS["champion_auc_drop"]["threshold"]:
                ops_data["alerts"].append(
                    {
                        "metric": "champion_auc_drop",
                        "model_type": champ["model_type"],
                        "current_value": drop,
                        "threshold": ALERT_THRESHOLDS["champion_auc_drop"]["threshold"],
                        "status": "firing",
                    }
                )

    return ops_data
