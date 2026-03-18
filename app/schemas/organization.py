"""Organization schemas."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field

from app.schemas.common import BaseSchema


class CreateOrganizationRequest(BaseModel):
    name: str = Field(max_length=200)
    short_name: str = Field(max_length=50, pattern=r"^[a-z0-9_-]+$")
    industry_type: str = Field(description="bank, fintech, mfi, sacco, other")
    primary_contact_name: str | None = Field(default=None, max_length=200)
    primary_contact_email: EmailStr | None = None
    primary_contact_phone: str | None = Field(default=None, max_length=20)
    address: str | None = None


class UpdateOrganizationRequest(BaseModel):
    name: str | None = Field(default=None, max_length=200)
    primary_contact_name: str | None = Field(default=None, max_length=200)
    primary_contact_email: EmailStr | None = None
    primary_contact_phone: str | None = Field(default=None, max_length=20)
    address: str | None = None


class OrganizationResponse(BaseSchema):
    id: UUID
    name: str
    short_name: str
    industry_type: str
    primary_contact_name: str | None
    primary_contact_email: str | None
    primary_contact_phone: str | None
    address: str | None
    status: str
    suspension_reason: str | None
    webhook_secret: str | None
    created_at: datetime
    updated_at: datetime


class ProvisionOrganizationRequest(BaseModel):
    callback_url: str | None = None


class ProvisionResponse(BaseModel):
    organization: OrganizationResponse
    admin_email: str
    sandbox_api_key: str
    production_api_key: str
    message: str = "Organization provisioned. Welcome email sent to admin."


class SuspendRequest(BaseModel):
    reason: str = Field(min_length=5, max_length=500)


class OrgUsageResponse(BaseModel):
    organization_id: UUID
    total_requests: int
    requests_today: int
    requests_this_week: int
    requests_this_month: int
    last_request_at: datetime | None
