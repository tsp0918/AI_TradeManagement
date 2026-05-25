"""add reexport license fields to plat_import_profile

Revision ID: l0m1n2o3p4q5
Revises: k9l0m1n2o3p4
Create Date: 2026-05-24

Phase III-3: US EAR 輸入品再輸出時の輸出許可申請自動トリガー。
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "l0m1n2o3p4q5"
down_revision = "k9l0m1n2o3p4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "plat_import_profile",
        sa.Column("reexport_license_id", UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "plat_import_profile",
        sa.Column("reexport_triggered_at", sa.DateTime, nullable=True),
    )


def downgrade() -> None:
    op.drop_column("plat_import_profile", "reexport_triggered_at")
    op.drop_column("plat_import_profile", "reexport_license_id")
