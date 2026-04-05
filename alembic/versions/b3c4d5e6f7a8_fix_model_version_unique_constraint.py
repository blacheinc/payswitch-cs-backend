"""Fix model_version unique constraint: version-only -> (model_type, version)

Each model type (credit_risk, fraud_detection, loan_amount, income_verification)
maintains its own version sequence. A global unique constraint on `version` alone
would reject e.g. credit_risk v1 and fraud_detection v1 as duplicates.

Revision ID: b3c4d5e6f7a8
Revises: a1b2c3d4e5f6
Create Date: 2026-04-03 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "b3c4d5e6f7a8"
down_revision: Union[str, None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Drop the old unique index on version alone
    op.drop_index(op.f("ix_model_versions_version"), table_name="model_versions")

    # Recreate as a non-unique index (still useful for lookups by version number)
    op.create_index(
        op.f("ix_model_versions_version"),
        "model_versions",
        ["version"],
        unique=False,
    )

    # Add composite unique constraint: one version per model type
    op.create_unique_constraint(
        "uq_model_versions_type_version",
        "model_versions",
        ["model_type", "version"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_model_versions_type_version", "model_versions", type_="unique"
    )

    op.drop_index(op.f("ix_model_versions_version"), table_name="model_versions")

    op.create_index(
        op.f("ix_model_versions_version"),
        "model_versions",
        ["version"],
        unique=True,
    )
