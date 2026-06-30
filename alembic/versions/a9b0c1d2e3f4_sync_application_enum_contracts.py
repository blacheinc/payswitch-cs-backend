"""Synchronize application enum contracts.

Revision ID: a9b0c1d2e3f4
Revises: f8a9b0c1d2e3
Create Date: 2026-06-30

The initial schema predates the value-based SQLAlchemy enum mappings used by
the current models. This migration preserves existing data while aligning the
database labels with those mappings and adding the current scoring decisions.
"""

from collections.abc import Sequence

from sqlalchemy import text

from alembic import op

revision: str = "a9b0c1d2e3f4"
down_revision: str | None = "f8a9b0c1d2e3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


ENUM_RENAMES = {
    "industry_type": {
        "BANK": "bank",
        "FINTECH": "fintech",
        "MFI": "mfi",
        "SACCO": "sacco",
        "OTHER": "other",
    },
    "source_type": {
        "BANK": "bank",
        "BUREAU": "bureau",
        "TELCO": "telco",
        "UTILITY": "utility",
        "OTHER": "other",
    },
    "training_data_status": {
        "PENDING": "pending",
        "PROCESSING": "processing",
        "COMPLETED": "completed",
        "APPROVED": "approved",
        "REJECTED": "rejected",
        "FAILED": "failed",
    },
    "decision_type": {
        "APPROVED": "approved",
        "DECLINED": "declined",
        "REFERRED": "referred",
    },
}

DECISION_VALUES = (
    "APPROVE",
    "CONDITIONAL_APPROVE",
    "REFER",
    "DECLINE",
    "FRAUD_HOLD",
    "ERROR",
)


def _labels(enum_name: str) -> set[str]:
    bind = op.get_bind()
    rows = bind.execute(
        text(
            "SELECT e.enumlabel FROM pg_enum e "
            "JOIN pg_type t ON t.oid = e.enumtypid "
            "WHERE t.typname = :enum_name"
        ),
        {"enum_name": enum_name},
    )
    return {row[0] for row in rows}


def upgrade() -> None:
    for enum_name, renames in ENUM_RENAMES.items():
        labels = _labels(enum_name)
        for old_value, new_value in renames.items():
            if old_value in labels and new_value not in labels:
                op.execute(
                    f"ALTER TYPE \"{enum_name}\" RENAME VALUE '{old_value}' TO '{new_value}'"
                )
                labels.remove(old_value)
                labels.add(new_value)

    decision_labels = _labels("decision_type")
    for value in DECISION_VALUES:
        if value not in decision_labels:
            op.execute(f"ALTER TYPE \"decision_type\" ADD VALUE '{value}'")


def downgrade() -> None:
    # This forward-compatible data migration is intentionally retained on
    # downgrade. PostgreSQL cannot remove enum labels without rebuilding every
    # dependent column, and the models support the retained legacy values.
    pass
