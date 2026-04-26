"""add export license application table

Revision ID: a1b2c3d4e5f6
Revises: f4a5b6c7d8e9
Create Date: 2026-04-26

輸出許可申請管理テーブルを追加する。
  plat_export_license_application — 申請ドラフト・期限管理
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "a1b2c3d4e5f6"
down_revision = "f4a5b6c7d8e9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "plat_export_license_application",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("application_number", sa.String(128), nullable=True),
        sa.Column("license_type", sa.String(16), nullable=False),
        sa.Column("form_type", sa.String(32), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="draft"),
        sa.Column("item_description", sa.Text(), nullable=True),
        sa.Column("eccn", sa.String(32), nullable=True),
        sa.Column("quantity", sa.Float(), nullable=True),
        sa.Column("quantity_unit", sa.String(32), nullable=True),
        sa.Column("value_usd", sa.Float(), nullable=True),
        sa.Column("destination_country", sa.String(4), nullable=True),
        sa.Column("consignee_name", sa.String(512), nullable=True),
        sa.Column("consignee_address", sa.Text(), nullable=True),
        sa.Column("end_use_description", sa.Text(), nullable=True),
        sa.Column("end_user_name", sa.String(512), nullable=True),
        sa.Column("draft_content", postgresql.JSONB(), nullable=True),
        sa.Column("transaction_ids", postgresql.JSONB(), nullable=True),
        sa.Column("supply_chain_node_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("license_number", sa.String(128), nullable=True),
        sa.Column("issuing_authority", sa.String(64), nullable=True),
        sa.Column("license_value_remaining_usd", sa.Float(), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("alert_sent", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_export_license_status", "plat_export_license_application", ["status"])
    op.create_index("ix_export_license_expires_at", "plat_export_license_application", ["expires_at"])


def downgrade() -> None:
    op.drop_index("ix_export_license_expires_at", table_name="plat_export_license_application")
    op.drop_index("ix_export_license_status", table_name="plat_export_license_application")
    op.drop_table("plat_export_license_application")
