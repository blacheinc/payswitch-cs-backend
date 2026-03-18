"""Schemas for API log responses."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class ApiLogResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    method: str
    path: str
    status_code: int
    response_time_ms: int
    ip_address: str | None = None
    user_agent: str | None = None
    request_id: str | None = None
    api_key_prefix: str | None = None
    error_detail: str | None = None
    created_at: datetime
