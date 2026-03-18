"""API Key schemas."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from app.schemas.common import BaseSchema


class CreateApiKeyRequest(BaseModel):
    name: str = Field(max_length=100)
    environment: str = Field(description="sandbox or production")


class ApiKeyResponse(BaseSchema):
    id: UUID
    key_prefix: str
    name: str
    environment: str
    status: str
    last_used_at: datetime | None
    created_at: datetime


class ApiKeyCreatedResponse(BaseModel):
    """Returned only on creation – full key shown once."""

    id: UUID
    key: str  # Full API key – shown ONLY once
    key_prefix: str
    name: str
    environment: str
    message: str = "Store this key securely. It will not be shown again."
