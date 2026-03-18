"""ASGI middleware – request ID injection, tenant context, CORS, API logging."""

from __future__ import annotations

import logging
import time
import uuid

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

logger = logging.getLogger(__name__)


class RequestIdMiddleware(BaseHTTPMiddleware):
    """Inject a unique request ID and persist API logs for /v1/ routes."""

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
        request.state.request_id = request_id

        start_time = time.perf_counter()
        response = await call_next(request)
        process_time = time.perf_counter() - start_time
        response_time_ms = int(process_time * 1000)

        response.headers["X-Request-ID"] = request_id
        response.headers["X-Process-Time"] = f"{process_time:.4f}"

        # Only log /v1/ organisation-facing routes
        path = request.url.path
        if path.startswith("/v1/"):
            try:
                await self._persist_api_log(
                    request, response, request_id, response_time_ms
                )
            except Exception:
                logger.warning("Failed to persist API log", exc_info=True)

        return response

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

        # Skip logging if we couldn't resolve an organisation (e.g. bad auth)
        if organization_id is None:
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
