"""Orchestrator proxy — HTTP client for sandbox rule evaluation.

Forwards validated requests to the DE inference function app
(same endpoint used by the scoring pipeline) and maps upstream
responses back to the client.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


class OrchestratorProxyError(Exception):
    """Raised when the orchestrator returns an error or is unreachable."""

    def __init__(self, status_code: int, body: dict[str, Any]):
        self.status_code = status_code
        self.body = body
        super().__init__(f"Orchestrator returned {status_code}")


async def forward_rule_evaluation(body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    """Forward a rule evaluation request to the DE inference endpoint.

    Uses the same DE_INFERENCE_URL and DE_INFERENCE_CODE that the
    scoring pipeline uses in score_requests.py.

    Args:
        body: Validated request body to forward as-is.

    Returns:
        Tuple of (status_code, response_body).

    Raises:
        OrchestratorProxyError: On upstream 500 or network failure (mapped to 502).
    """
    url = f"{settings.orchestrator_base_url.rstrip('/')}/api/v1/rules/evaluate"
    headers: dict[str, str] = {}
    if settings.orchestrator_function_key:
        headers["x-functions-key"] = settings.orchestrator_function_key

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(url, json=body, headers=headers)

            if resp.status_code == 200:
                return 200, resp.json()

            if resp.status_code in (400, 422):
                # Pass through upstream validation errors
                try:
                    error_body = resp.json()
                except Exception:
                    error_body = {
                        "error": "upstream_error",
                        "detail": resp.text[:500],
                    }
                return resp.status_code, error_body

            # Any other status (including 500) → 502
            logger.error(
                f"[orchestrator-proxy] Unexpected upstream status "
                f"{resp.status_code}: {resp.text[:300]}"
            )
            return 502, {
                "error": "upstream_error",
                "detail": "Orchestrator service unavailable",
            }

    except httpx.TimeoutException:
        logger.error("[orchestrator-proxy] Request timed out")
        return 502, {
            "error": "upstream_error",
            "detail": "Orchestrator service unavailable",
        }
    except httpx.ConnectError:
        logger.error("[orchestrator-proxy] Connection failed")
        return 502, {
            "error": "upstream_error",
            "detail": "Orchestrator service unavailable",
        }
    except Exception as e:
        logger.error(f"[orchestrator-proxy] Unexpected error: {e}")
        return 502, {
            "error": "upstream_error",
            "detail": "Orchestrator service unavailable",
        }
