"""Reconcile enums in databases imported from the shared DE schema.

Revision ID: b0c1d2e3f4a5
Revises: a9b0c1d2e3f4
Create Date: 2026-08-18

The Azure development database is assembled from both the Data Engineering
schema and this application's schema.  Several PostgreSQL enum type names are
shared, but the DE labels are not a superset of the API labels.  Keep the DE
labels for compatibility and add every value the API can persist or query.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "b0c1d2e3f4a5"
down_revision: str | None = "a9b0c1d2e3f4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


APPLICATION_ENUM_VALUES: dict[str, tuple[str, ...]] = {
    "admin_role": ("super_admin", "data_science", "operations"),
    "admin_user_status": ("active", "deactivated"),
    "api_key_environment": ("sandbox", "production"),
    "api_key_status": ("active", "revoked"),
    "batch_job_item_status": ("pending", "processing", "completed", "failed", "cancelled"),
    "bulk_job_status": (
        "validating",
        "queued",
        "processing",
        "completed",
        "failed",
        "cancelled",
    ),
    "confidence_level": ("green", "yellow", "red"),
    "data_source_type": ("bank", "bureau", "telco", "utility", "mfi", "other"),
    "decision_type": (
        "APPROVE",
        "CONDITIONAL_APPROVE",
        "REFER",
        "DECLINE",
        "FRAUD_HOLD",
        "ERROR",
        "PENDING",
        "approved",
        "declined",
        "referred",
    ),
    "file_format": ("csv", "json", "xlsx", "tsv", "txt"),
    "industry_type": ("bank", "fintech", "mfi", "sacco", "other"),
    "ingestion_status": ("pending", "ingesting", "complete", "failed"),
    "loan_performance_status": (
        "current",
        "past_due_30",
        "past_due_60",
        "past_due_90",
        "default",
        "paid_off",
        "write_off",
    ),
    "mapping_source": ("template", "llm", "manual"),
    "model_status": (
        "training",
        "validation",
        "staging",
        "production",
        "deprecated",
        "archived",
    ),
    "org_role_label": ("admin", "credit_officer", "developer", "viewer"),
    "org_status": ("pending", "active", "suspended"),
    "org_user_status": ("pending", "active", "suspended", "removed"),
    "request_source": ("api", "web_portal", "bulk"),
    "scoring_decision": (
        "APPROVE",
        "CONDITIONAL_APPROVE",
        "REFER",
        "DECLINE",
        "FRAUD_HOLD",
        "ERROR",
    ),
    "source_type": ("bank", "bureau", "telco", "utility", "other"),
    "training_data_status": (
        "pending",
        "processing",
        "completed",
        "approved",
        "rejected",
        "failed",
    ),
    "upload_status": (
        "ingesting",
        "ingested",
        "transforming",
        "transformed",
        "training",
        "trained",
        "error",
    ),
    "webhook_delivery_status": ("pending", "delivered", "failed"),
}


def upgrade() -> None:
    for enum_name, values in APPLICATION_ENUM_VALUES.items():
        for value in values:
            escaped_value = value.replace("'", "''")
            op.execute(f"ALTER TYPE \"{enum_name}\" ADD VALUE IF NOT EXISTS '{escaped_value}'")


def downgrade() -> None:
    # PostgreSQL cannot remove an enum label without rebuilding each dependent
    # column.  Retaining the added labels is backward compatible and preserves
    # rows written by this application.
    pass
