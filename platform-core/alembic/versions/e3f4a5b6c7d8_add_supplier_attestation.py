"""add supplier attestation table

Revision ID: e3f4a5b6c7d8
Revises: d2e3f4a5b6c7
Create Date: 2026-04-26

サプライヤー原産性証明テーブルを追加する。
  plat_supplier_attestation — BOM ノード別 ECCN/原産地申告・AI検証・審査証跡
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "e3f4a5b6c7d8"
down_revision = "d2e3f4a5b6c7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "plat_supplier_attestation",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "node_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("plat_supply_chain_node.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("supplier_name", sa.String(512), nullable=False),
        sa.Column("supplier_contact", sa.String(512), nullable=True),
        sa.Column("claimed_eccn", sa.String(30), nullable=True),
        sa.Column("claimed_country_of_origin", sa.String(3), nullable=True),
        sa.Column("claimed_us_content_pct", sa.Float(), nullable=True),
        sa.Column("is_us_origin_claimed", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("certificate_reference", sa.String(200), nullable=True),
        sa.Column("attestation_date", sa.Date(), nullable=True),
        sa.Column("expiry_date", sa.Date(), nullable=True),
        sa.Column("supporting_docs", postgresql.JSONB(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("ai_suggested_eccn", sa.String(30), nullable=True),
        sa.Column("ai_confidence", sa.Float(), nullable=True),
        sa.Column("ai_verdict", sa.String(20), nullable=True),
        sa.Column("ai_review_detail", postgresql.JSONB(), nullable=True),
        sa.Column("ai_reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reviewed_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("review_comment", sa.Text(), nullable=True),
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


def downgrade() -> None:
    op.drop_table("plat_supplier_attestation")
