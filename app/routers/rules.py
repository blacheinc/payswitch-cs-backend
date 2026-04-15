"""Router: POST /v1/rules/evaluate — sandbox rule evaluation proxy.

Validates input, forwards to the AI orchestrator function app,
and maps upstream responses back to the client.
"""

from __future__ import annotations

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import ValidationError as PydanticValidationError

from app.dependencies import AuthContext, get_auth_context
from app.schemas.monitoring import RuleEvaluateRequest
from app.services.orchestrator_proxy import forward_rule_evaluation

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/rules", tags=["Rules"])


@router.post("/evaluate")
async def evaluate_rules(
    body: RuleEvaluateRequest,
    auth: Annotated[AuthContext, Depends(get_auth_context)],
) -> JSONResponse:
    """Sandbox rule evaluation — proxy to orchestrator.

    Validates input (PD between 0-1, score_grade A-F), then forwards
    the request to the AI orchestrator. Does NOT persist anything.
    """
    # Pydantic already validates PD range (ge=0, le=1) and score_grade (pattern ^[A-F]$)
    # via the RuleEvaluateRequest schema. If we reach here, input is valid.

    status_code, response_body = await forward_rule_evaluation(
        body.model_dump(mode="json")
    )

    return JSONResponse(status_code=status_code, content=response_body)
