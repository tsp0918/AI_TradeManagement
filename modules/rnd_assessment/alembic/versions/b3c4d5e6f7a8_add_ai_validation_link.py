"""add ai_validation link to rd_case_profiles

Revision ID: b3c4d5e6f7a8
Revises: 9a1f2c3d4e55
Create Date: 2026-02-24 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa

revision = "b3c4d5e6f7a8"
down_revision = "a1b2c3d4e5f6"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "rd_case_profiles",
        sa.Column("ai_validation_transaction_id", sa.Integer(), nullable=True),
    )
    op.add_column(
        "rd_case_profiles",
        sa.Column("ai_validation_risk", sa.String(), nullable=True),
    )
    op.add_column(
        "rd_case_profiles",
        sa.Column("ai_validation_run_at", sa.DateTime(), nullable=True),
    )
    op.create_index(
        "ix_rd_case_profiles_ai_validation_transaction_id",
        "rd_case_profiles",
        ["ai_validation_transaction_id"],
    )


def downgrade():
    op.drop_index(
        "ix_rd_case_profiles_ai_validation_transaction_id",
        table_name="rd_case_profiles",
    )
    op.drop_column("rd_case_profiles", "ai_validation_run_at")
    op.drop_column("rd_case_profiles", "ai_validation_risk")
    op.drop_column("rd_case_profiles", "ai_validation_transaction_id")
