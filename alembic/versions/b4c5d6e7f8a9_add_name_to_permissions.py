"""add_name_to_permissions

Adds the user-friendly ``name`` column to the ``permissions`` table
(e.g. "Create API Key").  Existing rows are back-filled from the code
column so the NOT NULL constraint can be applied immediately.

Revision ID: b4c5d6e7f8a9
Revises: a3b4c5d6e7f8
Create Date: 2026-04-17
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b4c5d6e7f8a9"
down_revision: Union[str, None] = "a3b4c5d6e7f8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Add column as nullable so existing rows don't violate the constraint
    op.add_column(
        "permissions",
        sa.Column("name", sa.String(100), nullable=True),
    )

    # 2. Back-fill existing rows with their code value as a placeholder.
    #    The startup seeder will overwrite with proper human-readable names
    #    on next deploy, but we need *something* non-null right now.
    op.execute(
        "UPDATE permissions SET name = code WHERE name IS NULL"
    )

    # 3. Tighten to NOT NULL
    op.alter_column("permissions", "name", nullable=False)


def downgrade() -> None:
    op.drop_column("permissions", "name")
