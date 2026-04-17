"""api_logs_nullable_org_for_platform

Makes api_logs.organization_id nullable so that platform-level (admin) request
logs can be stored without an organization reference.

Revision ID: c5d6e7f8a9b0
Revises: b4c5d6e7f8a9
Create Date: 2026-04-17
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c5d6e7f8a9b0"
down_revision: Union[str, None] = "b4c5d6e7f8a9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        "api_logs",
        "organization_id",
        existing_type=postgresql.UUID(as_uuid=True),
        nullable=True,
    )


def downgrade() -> None:
    # NOTE: rows with organization_id IS NULL (platform logs) would violate
    # the NOT NULL constraint — delete them first if rolling back.
    op.execute("DELETE FROM api_logs WHERE organization_id IS NULL")
    op.alter_column(
        "api_logs",
        "organization_id",
        existing_type=postgresql.UUID(as_uuid=True),
        nullable=False,
    )
