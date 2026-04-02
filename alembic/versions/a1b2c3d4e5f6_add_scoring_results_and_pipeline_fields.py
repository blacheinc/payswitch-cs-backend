"""Add scoring_results table and ML pipeline columns

Revision ID: a1b2c3d4e5f6
Revises: ccf17ed4b835
Create Date: 2026-04-01 23:55:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, None] = "ccf17ed4b835"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── 1. Create scoring_decision enum ──────────────────────────────────
    scoring_decision_enum = postgresql.ENUM(
        "APPROVE", "CONDITIONAL_APPROVE", "REFER", "DECLINE", "FRAUD_HOLD", "ERROR",
        name="scoring_decision",
        create_type=False,
    )
    scoring_decision_enum.create(op.get_bind(), checkfirst=True)

    # ── 2. Create scoring_results table ──────────────────────────────────
    op.create_table(
        "scoring_results",
        sa.Column("request_id", sa.String(length=50), nullable=False),
        sa.Column(
            "score_request_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("score_requests.id"),
            nullable=True,
        ),
        sa.Column(
            "scoring_timestamp",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "decision",
            scoring_decision_enum,
            nullable=False,
        ),
        sa.Column("condition_applied", postgresql.JSONB(), nullable=True),
        sa.Column("credit_risk", postgresql.JSONB(), nullable=True),
        sa.Column("fraud_detection", postgresql.JSONB(), nullable=True),
        sa.Column("loan_amount", postgresql.JSONB(), nullable=True),
        sa.Column("income_verification", postgresql.JSONB(), nullable=True),
        sa.Column("scoring_metadata", postgresql.JSONB(), nullable=True),
        sa.Column("errors", postgresql.JSONB(), nullable=True),
        # Standard columns from UUIDPrimaryKeyMixin + TimestampMixin
        sa.Column("id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("request_id"),
    )
    op.create_index(
        op.f("ix_scoring_results_request_id"),
        "scoring_results",
        ["request_id"],
        unique=True,
    )
    op.create_index(
        op.f("ix_scoring_results_score_request_id"),
        "scoring_results",
        ["score_request_id"],
        unique=False,
    )

    # ── 3. Add training_id to model_versions ─────────────────────────────
    op.add_column(
        "model_versions",
        sa.Column(
            "training_id",
            sa.String(length=100),
            nullable=True,
            comment="AI pipeline training run ID, e.g. TRAIN-20260325-163000",
        ),
    )
    op.create_index(
        op.f("ix_model_versions_training_id"),
        "model_versions",
        ["training_id"],
        unique=False,
    )

    # ── 4. Add training_id + models_to_train to training_uploads ─────────
    op.add_column(
        "training_uploads",
        sa.Column(
            "training_id",
            sa.String(length=100),
            nullable=True,
            comment="AI pipeline training run ID, e.g. TRAIN-20260325-163000",
        ),
    )
    op.create_index(
        op.f("ix_training_uploads_training_id"),
        "training_uploads",
        ["training_id"],
        unique=False,
    )
    op.add_column(
        "training_uploads",
        sa.Column(
            "models_to_train",
            postgresql.JSONB(),
            nullable=True,
            comment="List of models requested for training",
        ),
    )


def downgrade() -> None:
    # ── Remove training_uploads columns ──────────────────────────────────
    op.drop_column("training_uploads", "models_to_train")
    op.drop_index(op.f("ix_training_uploads_training_id"), table_name="training_uploads")
    op.drop_column("training_uploads", "training_id")

    # ── Remove model_versions column ─────────────────────────────────────
    op.drop_index(op.f("ix_model_versions_training_id"), table_name="model_versions")
    op.drop_column("model_versions", "training_id")

    # ── Drop scoring_results table ───────────────────────────────────────
    op.drop_index(op.f("ix_scoring_results_score_request_id"), table_name="scoring_results")
    op.drop_index(op.f("ix_scoring_results_request_id"), table_name="scoring_results")
    op.drop_table("scoring_results")

    # ── Drop enum ────────────────────────────────────────────────────────
    sa.Enum(name="scoring_decision").drop(op.get_bind(), checkfirst=True)
