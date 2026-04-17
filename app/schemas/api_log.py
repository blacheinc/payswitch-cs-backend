"""Schemas for API log responses."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class ActorInfo(BaseModel):
    """Resolved identity of whoever made the request."""
    id: str
    # "org_user" | "admin_user" | "api_key"
    type: str
    name: str
    email: str | None = None


class ApiLogResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    # None for platform-scoped (admin) logs
    organization_id: UUID | None = None
    method: str
    path: str
    status_code: int
    response_time_ms: int
    ip_address: str | None = None
    user_agent: str | None = None
    request_id: str | None = None
    api_key_prefix: str | None = None
    error_detail: str | None = None
    # Populated via a post-fetch join — None when actor can no longer be resolved
    actor: ActorInfo | None = None
    created_at: datetime
