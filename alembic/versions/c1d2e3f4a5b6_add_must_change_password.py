"""add must_change_password to admin_users and org_users

Closes VAPT finding 6: accounts created with a system-generated password kept
that emailed credential valid indefinitely. The flag lets login force a change
before granting a usable session.

Existing rows default to false: they are already-established accounts whose
passwords were not necessarily emailed, and flagging them all would lock out
every current user on the next login.

Revision ID: c1d2e3f4a5b6
Revises: b0c1d2e3f4a5
Create Date: 2026-09-19
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "c1d2e3f4a5b6"
down_revision = "b0c1d2e3f4a5"
branch_labels = None
depends_on = None

_TABLES = ("admin_users", "org_users")


def upgrade() -> None:
    for table in _TABLES:
        op.add_column(
            table,
            sa.Column(
                "must_change_password",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            ),
        )
        # The server default is kept deliberately. The ORM supplies its own on
        # insert, but dropping it makes every raw-SQL insert that omits the
        # column fail the NOT NULL constraint — seeds, fixtures and console
        # inserts included.


def downgrade() -> None:
    for table in _TABLES:
        op.drop_column(table, "must_change_password")
