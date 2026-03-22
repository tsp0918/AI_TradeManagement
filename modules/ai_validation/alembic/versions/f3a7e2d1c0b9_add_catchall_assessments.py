"""add catchall_assessments table

Revision ID: f3a7e2d1c0b9
Revises: 16734207caef
Create Date: 2026-03-20 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f3a7e2d1c0b9"
down_revision: Union[str, None] = "16734207caef"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "catchall_assessments",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("transaction_id", sa.Integer(), nullable=False),
        sa.Column("session_id", sa.String(length=128), nullable=False),
        sa.Column("verdict", sa.String(length=32), nullable=False),
        sa.Column("verdict_label", sa.String(length=128), nullable=False, server_default=""),
        sa.Column("verdict_detail", sa.Text(), nullable=False, server_default=""),
        sa.Column("destination_country", sa.String(length=8), nullable=True),
        sa.Column("destination_risk_level", sa.String(length=16), nullable=True),
        sa.Column("is_concern_country", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("red_flag_positive_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("judgment_json", sa.JSON(), nullable=True),
        sa.Column("evaluated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["transaction_id"], ["transactions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_catchall_assessments_id", "catchall_assessments", ["id"])
    op.create_index(
        "ix_catchall_assessments_transaction_id",
        "catchall_assessments",
        ["transaction_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_catchall_assessments_transaction_id", table_name="catchall_assessments")
    op.drop_index("ix_catchall_assessments_id", table_name="catchall_assessments")
    op.drop_table("catchall_assessments")
