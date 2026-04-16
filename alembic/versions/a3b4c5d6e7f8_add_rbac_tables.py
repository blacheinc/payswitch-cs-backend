"""add_rbac_tables

Creates the full RBAC schema:
  - permissions
  - roles
  - role_permissions
  - org_user_roles
  - admin_user_roles
  - api_keys.role_id column

Revision ID: a3b4c5d6e7f8
Revises: 917c226495dc
Create Date: 2026-04-16
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "a3b4c5d6e7f8"
down_revision: Union[str, None] = "917c226495dc"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── permissions ───────────────────────────────────────────────────────────
    op.create_table(
        "permissions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("code", sa.String(100), nullable=False, unique=True),
        sa.Column("group_name", sa.String(50), nullable=False),
        sa.Column("description", sa.String(300), nullable=False),
        sa.Column("scope", sa.String(20), nullable=False),
    )
    op.create_index("ix_permissions_code", "permissions", ["code"], unique=True)
    op.create_index("ix_permissions_scope", "permissions", ["scope"])

    # ── roles ─────────────────────────────────────────────────────────────────
    op.create_table(
        "roles",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("description", sa.String(300), nullable=True),
        sa.Column("scope", sa.String(20), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("organizations.id"), nullable=True),
        sa.Column("is_system", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("name", "organization_id", name="uq_role_name_org"),
    )
    op.create_index("ix_roles_scope", "roles", ["scope"])
    op.create_index("ix_roles_organization_id", "roles", ["organization_id"])

    # ── role_permissions ──────────────────────────────────────────────────────
    op.create_table(
        "role_permissions",
        sa.Column("role_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("roles.id", ondelete="CASCADE"),
                  primary_key=True, nullable=False),
        sa.Column("permission_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("permissions.id", ondelete="CASCADE"),
                  primary_key=True, nullable=False),
    )

    # ── org_user_roles ────────────────────────────────────────────────────────
    op.create_table(
        "org_user_roles",
        sa.Column("org_user_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("org_users.id", ondelete="CASCADE"),
                  primary_key=True, nullable=False),
        sa.Column("role_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("roles.id", ondelete="CASCADE"),
                  primary_key=True, nullable=False),
        sa.Column("assigned_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("assigned_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
    )

    # ── admin_user_roles ──────────────────────────────────────────────────────
    op.create_table(
        "admin_user_roles",
        sa.Column("admin_user_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("admin_users.id", ondelete="CASCADE"),
                  primary_key=True, nullable=False),
        sa.Column("role_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("roles.id", ondelete="CASCADE"),
                  primary_key=True, nullable=False),
        sa.Column("assigned_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("assigned_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
    )

    # ── api_keys.role_id ──────────────────────────────────────────────────────
    op.add_column(
        "api_keys",
        sa.Column("role_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("roles.id", ondelete="SET NULL"),
                  nullable=True),
    )
    op.create_index("ix_api_keys_role_id", "api_keys", ["role_id"])


def downgrade() -> None:
    op.drop_index("ix_api_keys_role_id", table_name="api_keys")
    op.drop_column("api_keys", "role_id")

    op.drop_table("admin_user_roles")
    op.drop_table("org_user_roles")
    op.drop_table("role_permissions")
    op.drop_table("roles")
    op.drop_index("ix_permissions_scope", table_name="permissions")
    op.drop_index("ix_permissions_code", table_name="permissions")
    op.drop_table("permissions")
