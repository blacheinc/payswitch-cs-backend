"""backfill_org_admin_rbac

Backfill OrgUserRole assignments for any org user with role_label='admin'
who has no RBAC role linked.  This repairs orgs provisioned before the
provision flow was fixed to wire the system ADMIN role.

Revision ID: f8a9b0c1d2e3
Revises: e7f8a9b0c1d2
Create Date: 2026-05-26
"""

from typing import Sequence, Union

from alembic import op


revision: str = "f8a9b0c1d2e3"
down_revision: Union[str, None] = "e7f8a9b0c1d2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Insert an OrgUserRole row pointing every admin-labelled org user
    # (who has no existing RBAC role) at the system ADMIN role.
    op.execute(
        """
        INSERT INTO org_user_roles (org_user_id, role_id, assigned_by, assigned_at)
        SELECT
            ou.id,
            r.id,
            NULL,
            NOW()
        FROM org_users ou
        CROSS JOIN roles r
        WHERE r.name = 'ADMIN'
          AND r.is_system = TRUE
          AND r.scope = 'org'
          AND ou.role_label = 'admin'
          AND ou.status != 'removed'
          AND NOT EXISTS (
              SELECT 1 FROM org_user_roles our
              WHERE our.org_user_id = ou.id
          )
        """
    )


def downgrade() -> None:
    # No-op: we don't want to strip permissions from working users on rollback.
    pass
