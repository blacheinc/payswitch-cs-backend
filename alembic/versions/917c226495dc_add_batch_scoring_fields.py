"""add_batch_scoring_fields

Adds API-driven batch scoring support:
- bulk_scoring_jobs: job_id, models_to_run, results_blob_path, ingestion_status,
  ingested_count, failed_count; makes file_name/file_path nullable
- scoring_results: bulk_job_id FK to bulk_scoring_jobs

Revision ID: 917c226495dc
Revises: 48bac51cdad4
Create Date: 2026-04-15
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "917c226495dc"
down_revision: Union[str, None] = "48bac51cdad4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── bulk_scoring_jobs ─────────────────────────────────────────────────

    # New ingestion_status enum
    ingestion_status = postgresql.ENUM(
        "pending", "ingesting", "complete", "failed",
        name="ingestion_status",
        create_type=False,
    )
    ingestion_status.create(op.get_bind(), checkfirst=True)

    op.add_column(
        "bulk_scoring_jobs",
        sa.Column("job_id", sa.String(100), nullable=True),
    )
    op.create_index(
        "ix_bulk_scoring_jobs_job_id", "bulk_scoring_jobs", ["job_id"], unique=True
    )
    op.add_column(
        "bulk_scoring_jobs",
        sa.Column("models_to_run", postgresql.JSONB(), nullable=True),
    )
    op.add_column(
        "bulk_scoring_jobs",
        sa.Column("results_blob_path", sa.String(500), nullable=True),
    )
    op.add_column(
        "bulk_scoring_jobs",
        sa.Column(
            "ingestion_status",
            sa.Enum(
                "pending", "ingesting", "complete", "failed",
                name="ingestion_status",
            ),
            nullable=False,
            server_default="pending",
        ),
    )
    op.add_column(
        "bulk_scoring_jobs",
        sa.Column("ingested_count", sa.Integer(), nullable=False, server_default="0"),
    )

    # Make file_name / file_path nullable (not required for API-submitted jobs)
    op.alter_column("bulk_scoring_jobs", "file_name", nullable=True)
    op.alter_column("bulk_scoring_jobs", "file_path", nullable=True)

    # ── scoring_results ───────────────────────────────────────────────────

    op.add_column(
        "scoring_results",
        sa.Column(
            "bulk_job_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("bulk_scoring_jobs.id"),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_scoring_results_bulk_job_id", "scoring_results", ["bulk_job_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_scoring_results_bulk_job_id", table_name="scoring_results")
    op.drop_column("scoring_results", "bulk_job_id")

    op.drop_index("ix_bulk_scoring_jobs_job_id", table_name="bulk_scoring_jobs")
    op.drop_column("bulk_scoring_jobs", "job_id")
    op.drop_column("bulk_scoring_jobs", "models_to_run")
    op.drop_column("bulk_scoring_jobs", "results_blob_path")
    op.drop_column("bulk_scoring_jobs", "ingestion_status")
    op.drop_column("bulk_scoring_jobs", "ingested_count")

    op.alter_column("bulk_scoring_jobs", "file_name", nullable=False)
    op.alter_column("bulk_scoring_jobs", "file_path", nullable=False)

    op.execute("DROP TYPE IF EXISTS ingestion_status")
