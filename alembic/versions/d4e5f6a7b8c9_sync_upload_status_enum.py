"""sync_upload_status_enum

Revision ID: d4e5f6a7b8c9
Revises: c921a283201d
Create Date: 2026-03-03 21:06:00.000000

"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d4e5f6a7b8c9"
down_revision: Union[str, None] = "c921a283201d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Old → New status mapping
STATUS_MAP = {
    "UPLOADING": "ingesting",
    "PARSING": "ingesting",
    "PARSED": "ingesting",
    "MAPPING": "ingesting",
    "MAPPED": "ingested",
    "REVIEWING": "ingested",
    "APPROVED": "ingested",
    "TRANSFORMING": "transforming",
    "COMPLETED": "trained",
    "REJECTED": "ingested",
    "FAILED": "ingesting",
}

NEW_VALUES = (
    "ingesting",
    "ingested",
    "transforming",
    "transformed",
    "training",
    "trained",
)


def upgrade() -> None:
    # 1. Create the new enum type
    op.execute(
        "CREATE TYPE upload_status_new AS ENUM (%s)"
        % ", ".join(f"'{v}'" for v in NEW_VALUES)
    )

    # 2. Migrate existing rows to new values using a temp text column
    op.execute("ALTER TABLE training_uploads ALTER COLUMN status TYPE text")

    for old_val, new_val in STATUS_MAP.items():
        op.execute(
            f"UPDATE training_uploads SET status = '{new_val}' WHERE status = '{old_val}'"
        )

    # 3. Cast the text column to the new enum type
    op.execute(
        "ALTER TABLE training_uploads ALTER COLUMN status TYPE upload_status_new "
        "USING status::upload_status_new"
    )

    # 4. Drop the old enum and rename the new one
    op.execute("DROP TYPE upload_status")
    op.execute("ALTER TYPE upload_status_new RENAME TO upload_status")


def downgrade() -> None:
    # Reverse: recreate old enum
    old_values = (
        "UPLOADING",
        "PARSING",
        "PARSED",
        "MAPPING",
        "MAPPED",
        "REVIEWING",
        "APPROVED",
        "TRANSFORMING",
        "COMPLETED",
        "REJECTED",
        "FAILED",
    )
    op.execute(
        "CREATE TYPE upload_status_old AS ENUM (%s)"
        % ", ".join(f"'{v}'" for v in old_values)
    )
    op.execute("ALTER TABLE training_uploads ALTER COLUMN status TYPE text")
    op.execute(
        "UPDATE training_uploads SET status = 'UPLOADING' WHERE status = 'ingesting'"
    )
    op.execute(
        "UPDATE training_uploads SET status = 'MAPPED' WHERE status = 'ingested'"
    )
    op.execute(
        "UPDATE training_uploads SET status = 'TRANSFORMING' WHERE status = 'transforming'"
    )
    op.execute(
        "UPDATE training_uploads SET status = 'TRANSFORMING' WHERE status = 'transformed'"
    )
    op.execute(
        "UPDATE training_uploads SET status = 'COMPLETED' WHERE status = 'trained'"
    )
    op.execute(
        "ALTER TABLE training_uploads ALTER COLUMN status TYPE upload_status_old "
        "USING status::upload_status_old"
    )
    op.execute("DROP TYPE upload_status")
    op.execute("ALTER TYPE upload_status_old RENAME TO upload_status")
