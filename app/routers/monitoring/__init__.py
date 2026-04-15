"""Monitoring router package — aggregates all monitoring sub-routers.

Provides a single `monitoring_router` to include in the main app.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.routers.monitoring.alerts import router as alerts_router
from app.routers.monitoring.compliance import router as compliance_router
from app.routers.monitoring.infrastructure import router as infrastructure_router
from app.routers.monitoring.model_ops import router as model_ops_router
from app.routers.monitoring.risk import router as risk_router

monitoring_router = APIRouter(prefix="/v1/monitoring", tags=["Monitoring"])

monitoring_router.include_router(infrastructure_router)
monitoring_router.include_router(risk_router)
monitoring_router.include_router(model_ops_router)
monitoring_router.include_router(compliance_router)
monitoring_router.include_router(alerts_router)
