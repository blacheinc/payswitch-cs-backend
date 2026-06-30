"""ASGI middleware – request ID injection, tenant context, CORS, API logging."""

from __future__ import annotations

import asyncio
import logging
import time
import uuid

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

logger = logging.getLogger(__name__)

# Simple counter — every INFRA_ALERT_EVERY requests we re-evaluate infra alerts.
# Single-threaded async event loop makes plain int safe here.
_request_count: int = 0
INFRA_ALERT_EVERY: int = 50


async def _run_infra_alerts() -> None:
    """Background task: evaluate infra alerts in an isolated session."""
    try:
        from app.db import async_session_factory
        from app.jobs.alert_evaluator import evaluate_infra_alerts

        async with async_session_factory() as db:
            async with db.begin():
                await evaluate_infra_alerts(db)
    except Exception:
        logger.warning("[middleware] Infra alert evaluation failed", exc_info=True)


class RequestIdMiddleware(BaseHTTPMiddleware):
    """Inject a unique request ID and persist API logs for /v1/ routes."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
        request.state.request_id = request_id

        start_time = time.perf_counter()
        response = await call_next(request)
        process_time = time.perf_counter() - start_time
        response_time_ms = int(process_time * 1000)

        response.headers["X-Request-ID"] = request_id
        response.headers["X-Process-Time"] = f"{process_time:.4f}"

        # Record org-agnostic request metrics for infrastructure dashboard
        try:
            await self._persist_request_metric(request, response, request_id, response_time_ms)
        except Exception:
            logger.warning("Failed to persist request metric", exc_info=True)

        # Log org-facing (/v1/) and platform-facing (/admin/) authenticated routes
        path = request.url.path
        if path.startswith("/v1/") or path.startswith("/admin/"):
            try:
                await self._persist_api_log(request, response, request_id, response_time_ms)
            except Exception:
                logger.warning("Failed to persist API log", exc_info=True)

        return response

    async def _persist_request_metric(
        self,
        request: Request,
        response: Response,
        request_id: str,
        response_time_ms: int,
    ) -> None:
        """Write a lightweight RequestMetric row for infrastructure dashboard."""
        global _request_count

        from app.db import async_session_factory
        from app.models.request_metric import RequestMetric

        async with async_session_factory() as session:
            metric = RequestMetric(
                endpoint=request.url.path[:500],
                method=request.method,
                status_code=response.status_code,
                response_time_ms=response_time_ms,
                request_id=request_id,
            )
            session.add(metric)
            await session.commit()

        _request_count += 1
        if _request_count % INFRA_ALERT_EVERY == 0:
            asyncio.create_task(_run_infra_alerts())

    async def _persist_api_log(
        self,
        request: Request,
        response: Response,
        request_id: str,
        response_time_ms: int,
    ) -> None:
        # Extract auth context set by dependencies (may not exist on 401s)
        organization_id = getattr(request.state, "organization_id", None)
        api_key_id = getattr(request.state, "api_key_id", None)
        api_key_prefix = getattr(request.state, "api_key_prefix", None)
        user_id = getattr(request.state, "user_id", None)

        # Skip if neither an org nor a user could be resolved (e.g. bad auth,
        # health-check endpoints, unauthenticated 401s).
        if organization_id is None and user_id is None:
            return

        # Extract error detail for 4xx/5xx
        error_detail = None
        if response.status_code >= 400:
            # We store the status code; detailed error is in the response body
            # which we don't capture here to avoid buffering.
            error_detail = f"HTTP {response.status_code}"

        from app.db import async_session_factory
        from app.models.api_log import ApiLog

        async with async_session_factory() as session:
            log_entry = ApiLog(
                organization_id=organization_id,
                method=request.method,
                path=request.url.path,
                status_code=response.status_code,
                response_time_ms=response_time_ms,
                ip_address=request.client.host if request.client else None,
                user_agent=request.headers.get("User-Agent", "")[:500],
                request_id=request_id,
                api_key_id=api_key_id,
                api_key_prefix=api_key_prefix,
                user_id=user_id,
                error_detail=error_detail,
            )
            session.add(log_entry)
            await session.commit()
