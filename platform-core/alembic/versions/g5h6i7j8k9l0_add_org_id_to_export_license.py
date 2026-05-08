"""add org_id to export_license_application and supply_chain_node

Revision ID: g5h6i7j8k9l0
Revises: f5g6h7i8j9k0
Create Date: 2026-05-08 18:10:00.000000
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = 'g5h6i7j8k9l0'
down_revision = 'f5g6h7i8j9k0'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "plat_export_license_application",
        sa.Column("org_id", UUID(as_uuid=True), nullable=True),
    )
    op.create_index(
        "ix_plat_export_license_application_org_id",
        "plat_export_license_application",
        ["org_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_plat_export_license_application_org_id",
        table_name="plat_export_license_application",
    )
    op.drop_column("plat_export_license_application", "org_id")
