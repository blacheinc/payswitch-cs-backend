"""Add api_logs table

Revision ID: ffa143cc48ff
Revises: 9f189d110c34
Create Date: 2026-02-12 21:59:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "ffa143cc48ff"
down_revision: Union[str, None] = "9f189d110c34"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "api_logs",
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("method", sa.String(length=10), nullable=False),
        sa.Column("path", sa.String(length=500), nullable=False),
        sa.Column("status_code", sa.Integer(), nullable=False),
        sa.Column("response_time_ms", sa.Integer(), nullable=False),
        sa.Column("ip_address", sa.String(length=45), nullable=True),
        sa.Column("user_agent", sa.String(length=500), nullable=True),
        sa.Column("request_id", sa.String(length=50), nullable=True),
        sa.Column("api_key_id", sa.UUID(), nullable=True),
        sa.Column("api_key_prefix", sa.String(length=12), nullable=True),
        sa.Column("user_id", sa.UUID(), nullable=True),
        sa.Column("error_detail", sa.Text(), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
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
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_api_logs_organization_id"),
        "api_logs",
        ["organization_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_api_logs_status_code"),
        "api_logs",
        ["status_code"],
        unique=False,
    )
    op.create_index(
        op.f("ix_api_logs_created_at"),
        "api_logs",
        ["created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_api_logs_created_at"), table_name="api_logs")
    op.drop_index(op.f("ix_api_logs_status_code"), table_name="api_logs")
    op.drop_index(op.f("ix_api_logs_organization_id"), table_name="api_logs")
    op.drop_table("api_logs")
