"""add_batch_job_items

Creates the batch_job_items table for per-applicant tracking in the
redesigned batch scoring flow.  Items are enqueued individually into Redis
and processed by internal workers rather than delegated to an external
ML orchestrator.

Revision ID: d6e7f8a9b0c1
Revises: c5d6e7f8a9b0
Create Date: 2026-04-17
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "d6e7f8a9b0c1"
down_revision: Union[str, None] = "c5d6e7f8a9b0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "batch_job_items",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "batch_job_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("bulk_scoring_jobs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("index", sa.Integer, nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "pending", "processing", "completed", "failed", "cancelled",
                name="batch_job_item_status",
            ),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("payload", postgresql.JSONB, nullable=False),
        sa.Column(
            "score_request_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("score_requests.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("score_tracking_id", sa.String(50), nullable=True),
        sa.Column("error_code", sa.String(50), nullable=True),
        sa.Column("error_message", sa.Text, nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            onupdate=sa.text("now()"),
            nullable=False,
        ),
    )

    op.create_index("ix_batch_job_items_batch_job_id", "batch_job_items", ["batch_job_id"])
    op.create_index("ix_batch_job_items_status", "batch_job_items", ["status"])
    op.create_index("ix_batch_job_items_score_request_id", "batch_job_items", ["score_request_id"])


def downgrade() -> None:
    op.drop_index("ix_batch_job_items_score_request_id", "batch_job_items")
    op.drop_index("ix_batch_job_items_status", "batch_job_items")
    op.drop_index("ix_batch_job_items_batch_job_id", "batch_job_items")
    op.drop_table("batch_job_items")
    op.execute("DROP TYPE IF EXISTS batch_job_item_status")
