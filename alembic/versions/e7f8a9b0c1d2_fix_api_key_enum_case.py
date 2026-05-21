"""fix_api_key_enum_case

Normalise api_key_environment and api_key_status enum values to lowercase
to match the Python model definitions.  These two were accidentally omitted
from the earlier ccf17ed4b835_normalize_enums_to_lowercase migration.

Revision ID: e7f8a9b0c1d2
Revises: d6e7f8a9b0c1
Create Date: 2026-05-21
"""

from typing import Sequence, Union

from alembic import op

revision: str = "e7f8a9b0c1d2"
down_revision: Union[str, None] = "d6e7f8a9b0c1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TYPE api_key_environment RENAME VALUE 'SANDBOX' TO 'sandbox'")
    op.execute("ALTER TYPE api_key_environment RENAME VALUE 'PRODUCTION' TO 'production'")
    op.execute("ALTER TYPE api_key_status RENAME VALUE 'ACTIVE' TO 'active'")
    op.execute("ALTER TYPE api_key_status RENAME VALUE 'REVOKED' TO 'revoked'")


def downgrade() -> None:
    op.execute("ALTER TYPE api_key_environment RENAME VALUE 'sandbox' TO 'SANDBOX'")
    op.execute("ALTER TYPE api_key_environment RENAME VALUE 'production' TO 'PRODUCTION'")
    op.execute("ALTER TYPE api_key_status RENAME VALUE 'active' TO 'ACTIVE'")
    op.execute("ALTER TYPE api_key_status RENAME VALUE 'revoked' TO 'REVOKED'")
