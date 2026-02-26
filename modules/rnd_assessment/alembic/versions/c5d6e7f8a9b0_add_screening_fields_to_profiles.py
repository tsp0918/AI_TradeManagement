"""add screening fields to rd_case_profiles

Revision ID: c5d6e7f8a9b0
Revises: b3c4d5e6f7a8
Create Date: 2026-02-26 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa

revision = "c5d6e7f8a9b0"
down_revision = "b3c4d5e6f7a8"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "rd_case_profiles",
        sa.Column("screening_result_id", sa.String(36), nullable=True),
    )
    op.add_column(
        "rd_case_profiles",
        sa.Column("screening_status", sa.String(30), nullable=True),
    )


def downgrade():
    op.drop_column("rd_case_profiles", "screening_status")
    op.drop_column("rd_case_profiles", "screening_result_id")
