"""add_password_reset_to_admin_users

Revision ID: f7bf1ad18f53
Revises: ffa143cc48ff
Create Date: 2026-02-19 02:00:05.716030

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "f7bf1ad18f53"
down_revision: Union[str, None] = "ffa143cc48ff"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "admin_users",
        sa.Column("password_reset_token", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "admin_users",
        sa.Column("password_reset_expires", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("admin_users", "password_reset_expires")
    op.drop_column("admin_users", "password_reset_token")
