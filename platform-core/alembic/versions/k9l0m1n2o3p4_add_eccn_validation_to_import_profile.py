"""add eccn validation fields to plat_import_profile

Revision ID: k9l0m1n2o3p4
Revises: j8k9l0m1n2o3
Create Date: 2026-05-24

Phase III-1: 輸入品 ECCN 付番フロー。ai_validation との連携カラム追加。
"""
from alembic import op
import sqlalchemy as sa

revision = "k9l0m1n2o3p4"
down_revision = "j8k9l0m1n2o3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "plat_import_profile",
        sa.Column("eccn_validation_tx_id", sa.Integer, nullable=True),
    )
    op.add_column(
        "plat_import_profile",
        sa.Column("eccn_requested_at", sa.DateTime, nullable=True),
    )
    op.add_column(
        "plat_import_profile",
        sa.Column("eccn_judgment_status", sa.String(32), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("plat_import_profile", "eccn_judgment_status")
    op.drop_column("plat_import_profile", "eccn_requested_at")
    op.drop_column("plat_import_profile", "eccn_validation_tx_id")
