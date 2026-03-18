"""Webhook schemas."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field, HttpUrl

from app.schemas.common import BaseSchema


class CreateWebhookRequest(BaseModel):
    url: str = Field(max_length=500)
    events: list[str] = Field(default=["score.completed"])
    description: str | None = Field(default=None, max_length=200)


class UpdateWebhookRequest(BaseModel):
    url: str | None = Field(default=None, max_length=500)
    events: list[str] | None = None
    is_active: bool | None = None
    description: str | None = None


class WebhookResponse(BaseSchema):
    id: UUID
    url: str
    events: list[str]
    is_active: bool
    description: str | None
    created_at: datetime


class WebhookTestResponse(BaseModel):
    message: str = "Test webhook sent"
    delivery_id: UUID | None = None
    status_code: int | None = None
