"""Score Request, Score Decision, and Loan Performance models."""

from __future__ import annotations

import enum
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import Date, DateTime, Enum, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.api_key import ApiKey
    from app.models.organization import Organization
    from app.models.user import OrgUser


class ScoreRequestStatus(str, enum.Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class RequestSource(str, enum.Enum):
    API = "api"
    WEB_PORTAL = "web_portal"
    BULK = "bulk"


class ScoreRequest(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "score_requests"

    tracking_id: Mapped[str] = mapped_column(
        String(50), unique=True, nullable=False, index=True
    )
    organization_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id"), nullable=False, index=True
    )
    reference_id: Mapped[str | None] = mapped_column(String(100), index=True)
    status: Mapped[ScoreRequestStatus] = mapped_column(
        Enum(
            ScoreRequestStatus,
            name="score_request_status",
            values_callable=lambda x: [e.value for e in x],
        ),
        default=ScoreRequestStatus.PENDING,
        nullable=False,
        index=True,
    )
    request_source: Mapped[RequestSource] = mapped_column(
        Enum(
            RequestSource,
            name="request_source",
            values_callable=lambda x: [e.value for e in x],
        ),
        nullable=False,
    )
    # Full request payload (encrypted at rest by PG/Azure)
    request_payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    # Hash of applicant identifiers for deduplication
    applicant_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    # Full score response (stored in PG for fast access, also in Cosmos for archival)
    score_result: Mapped[dict | None] = mapped_column(JSONB)
    # Denormalized fields for fast queries
    score_value: Mapped[int | None] = mapped_column(Integer)
    risk_category: Mapped[str | None] = mapped_column(String(20))
    confidence: Mapped[float | None] = mapped_column()
    model_version: Mapped[str | None] = mapped_column(String(50))
    processing_time_ms: Mapped[int | None] = mapped_column(Integer)
    # Who made the request
    api_key_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("api_keys.id")
    )
    created_by_user_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("org_users.id")
    )
    # Idempotency
    idempotency_key: Mapped[str | None] = mapped_column(
        String(100), unique=True, index=True
    )
    # Timestamps
    scored_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    valid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Callback
    callback_url: Mapped[str | None] = mapped_column(String(500))

    # Relationships
    organization: Mapped["Organization"] = relationship(back_populates="score_requests")
    api_key: Mapped["ApiKey | None"] = relationship()
    created_by: Mapped["OrgUser | None"] = relationship()
    decision: Mapped["ScoreDecision | None"] = relationship(
        back_populates="score_request", uselist=False
    )
    loan_performances: Mapped[list["LoanPerformance"]] = relationship(
        back_populates="score_request"
    )

    def __repr__(self) -> str:
        return f"<ScoreRequest {self.tracking_id} ({self.status.value})>"


class DecisionType(str, enum.Enum):
    APPROVED = "approved"
    DECLINED = "declined"
    REFERRED = "referred"
    PENDING = "pending"


class ScoreDecision(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "score_decisions"

    score_request_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True), ForeignKey("score_requests.id"), unique=True, nullable=False
    )
    decision: Mapped[DecisionType] = mapped_column(
        Enum(
            DecisionType,
            name="decision_type",
            values_callable=lambda x: [e.value for e in x],
        ),
        nullable=False,
    )
    approved_amount: Mapped[Decimal | None] = mapped_column(Numeric(15, 2))
    approved_tenure: Mapped[int | None] = mapped_column(Integer)
    interest_rate: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))
    decline_reason: Mapped[str | None] = mapped_column(Text)
    decision_notes: Mapped[str | None] = mapped_column(Text)
    decided_by_user_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("org_users.id")
    )
    decided_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    # Relationships
    score_request: Mapped["ScoreRequest"] = relationship(back_populates="decision")
    decided_by: Mapped["OrgUser | None"] = relationship()


class LoanPerformanceStatus(str, enum.Enum):
    CURRENT = "current"
    PAST_DUE_30 = "past_due_30"
    PAST_DUE_60 = "past_due_60"
    PAST_DUE_90 = "past_due_90"
    DEFAULT = "default"
    PAID_OFF = "paid_off"
    WRITE_OFF = "write_off"


class LoanPerformance(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "loan_performances"

    score_request_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True), ForeignKey("score_requests.id"), nullable=False, index=True
    )
    status: Mapped[LoanPerformanceStatus] = mapped_column(
        Enum(
            LoanPerformanceStatus,
            name="loan_performance_status",
            values_callable=lambda x: [e.value for e in x],
        ),
        nullable=False,
    )
    days_past_due: Mapped[int | None] = mapped_column(Integer)
    outstanding_balance: Mapped[Decimal | None] = mapped_column(Numeric(15, 2))
    as_of_date: Mapped[datetime] = mapped_column(Date, nullable=False)
    reported_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    # Relationships
    score_request: Mapped["ScoreRequest"] = relationship(
        back_populates="loan_performances"
    )
