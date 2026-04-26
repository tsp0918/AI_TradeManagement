"""add item version and compliance change event tables

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-04-26

品目バージョン管理 + 仕様変更コンプライアンス影響検知テーブルを追加する。
  plat_item_version         — 品目仕様スナップショット（バージョン履歴）
  plat_compliance_change_event — 仕様変更コンプライアンス影響イベント
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "b2c3d4e5f6a7"
down_revision = "a1b2c3d4e5f6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "plat_item_version",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("item_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("version_label", sa.String(128), nullable=True),
        sa.Column("eccn", sa.String(32), nullable=True),
        sa.Column("hs_code", sa.String(20), nullable=True),
        sa.Column("country_of_origin", sa.String(4), nullable=True),
        sa.Column("manufacturer", sa.String(512), nullable=True),
        sa.Column("us_content_pct", sa.Float(), nullable=True),
        sa.Column("supplier_ids", postgresql.JSONB(), nullable=True),
        sa.Column("manufacturing_process", sa.Text(), nullable=True),
        sa.Column("chemical_composition", sa.Text(), nullable=True),
        sa.Column("spec_snapshot", postgresql.JSONB(), nullable=True),
        sa.Column("change_category", sa.String(64), nullable=True),
        sa.Column("change_reason", sa.Text(), nullable=True),
        sa.Column("source_system", sa.String(64), nullable=True),
        sa.Column("source_ref", sa.String(256), nullable=True),
        sa.Column("is_current", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_item_version_item_id", "plat_item_version", ["item_id"])
    op.create_index("ix_item_version_change_category", "plat_item_version", ["change_category"])

    op.create_table(
        "plat_compliance_change_event",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("item_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("from_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("to_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("change_category", sa.String(64), nullable=False),
        sa.Column("impact_level", sa.String(16), nullable=False),
        sa.Column("impact_details", postgresql.JSONB(), nullable=True),
        sa.Column("action_required", postgresql.JSONB(), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="open"),
        sa.Column("assessed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("resolution_notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_compliance_event_item_id", "plat_compliance_change_event", ["item_id"])
    op.create_index("ix_compliance_event_status", "plat_compliance_change_event", ["status"])
    op.create_index("ix_compliance_event_impact_level", "plat_compliance_change_event", ["impact_level"])
    op.create_index("ix_compliance_event_change_category", "plat_compliance_change_event", ["change_category"])


def downgrade() -> None:
    op.drop_index("ix_compliance_event_change_category", table_name="plat_compliance_change_event")
    op.drop_index("ix_compliance_event_impact_level", table_name="plat_compliance_change_event")
    op.drop_index("ix_compliance_event_status", table_name="plat_compliance_change_event")
    op.drop_index("ix_compliance_event_item_id", table_name="plat_compliance_change_event")
    op.drop_table("plat_compliance_change_event")

    op.drop_index("ix_item_version_change_category", table_name="plat_item_version")
    op.drop_index("ix_item_version_item_id", table_name="plat_item_version")
    op.drop_table("plat_item_version")
