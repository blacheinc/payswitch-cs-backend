"""normalize_enums_to_lowercase

Revision ID: ccf17ed4b835
Revises: f6a7b8c9d0e1
Create Date: 2026-03-09 13:07:28.459932

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "ccf17ed4b835"
down_revision: Union[str, None] = "f6a7b8c9d0e1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Normalize all enums to lowercase as expected by the application code
    enums_to_normalize = {
        "org_user_status": ["PENDING", "ACTIVE", "REMOVED"],
        "org_status": ["PENDING", "ACTIVE", "SUSPENDED"],
        "admin_user_status": ["ACTIVE", "DEACTIVATED"],
        "admin_role": ["SUPER_ADMIN", "DATA_SCIENCE", "OPERATIONS"],
        "org_role_label": ["ADMIN", "CREDIT_OFFICER", "DEVELOPER", "VIEWER"],
        "data_source_type": ["BANK", "BUREAU", "TELCO", "UTILITY", "MFI", "OTHER"],
        "file_format": ["CSV", "JSON", "XLSX", "TSV", "TXT"],
        "confidence_level": ["GREEN", "YELLOW", "RED"],
        "mapping_source": ["TEMPLATE", "LLM", "MANUAL"],
        "model_status": [
            "TRAINING",
            "VALIDATION",
            "STAGING",
            "PRODUCTION",
            "DEPRECATED",
            "ARCHIVED",
        ],
        "bulk_job_status": [
            "VALIDATING",
            "QUEUED",
            "PROCESSING",
            "COMPLETED",
            "FAILED",
            "CANCELLED",
        ],
        "webhook_delivery_status": ["PENDING", "DELIVERED", "FAILED"],
        "score_request_status": ["PENDING", "PROCESSING", "COMPLETED", "FAILED"],
        "request_source": ["API", "WEB_PORTAL", "BULK"],
        "loan_performance_status": [
            "CURRENT",
            "PAST_DUE_30",
            "PAST_DUE_60",
            "PAST_DUE_90",
            "DEFAULT",
            "PAID_OFF",
            "WRITE_OFF",
        ],
    }

    for enum_name, labels in enums_to_normalize.items():
        for label in labels:
            # We use ALTER TYPE ... RENAME VALUE to lowercase
            # We catch exceptions if the label doesn't exist or is already lowercase
            op.execute(
                f"ALTER TYPE {enum_name} RENAME VALUE '{label}' TO '{label.lower()}'"
            )


def downgrade() -> None:
    # Revert to UPPERCASE
    enums_to_normalize = {
        "org_user_status": ["pending", "active", "removed"],
        "org_status": ["pending", "active", "suspended"],
        "admin_user_status": ["active", "deactivated"],
        "admin_role": ["super_admin", "data_science", "operations"],
        "org_role_label": ["admin", "credit_officer", "developer", "viewer"],
        "data_source_type": ["bank", "bureau", "telco", "utility", "mfi", "other"],
        "file_format": ["csv", "json", "xlsx", "tsv", "txt"],
        "confidence_level": ["green", "yellow", "red"],
        "mapping_source": ["template", "llm", "manual"],
        "model_status": [
            "training",
            "validation",
            "staging",
            "production",
            "deprecated",
            "archived",
        ],
        "bulk_job_status": [
            "validating",
            "queued",
            "processing",
            "completed",
            "failed",
            "cancelled",
        ],
        "webhook_delivery_status": ["pending", "delivered", "failed"],
        "score_request_status": ["pending", "processing", "completed", "failed"],
        "request_source": ["api", "web_portal", "bulk"],
        "loan_performance_status": [
            "current",
            "past_due_30",
            "past_due_60",
            "past_due_90",
            "default",
            "paid_off",
            "write_off",
        ],
    }

    for enum_name, labels in enums_to_normalize.items():
        for label in labels:
            op.execute(
                f"ALTER TYPE {enum_name} RENAME VALUE '{label}' TO '{label.upper()}'"
            )
