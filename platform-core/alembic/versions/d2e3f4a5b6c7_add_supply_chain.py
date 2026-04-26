"""add supply chain nodes and edges tables

Revision ID: d2e3f4a5b6c7
Revises: c1e2f3a4b5d6
Create Date: 2026-04-26

サプライチェーン管理（BOM構造 + De Minimis 計算基盤）のテーブルを追加する。
  plat_supply_chain_node  — 製品/部品/素材ノード
  plat_supply_chain_edge  — 親子 BOM エッジ
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "d2e3f4a5b6c7"
down_revision = "c1e2f3a4b5d6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "plat_supply_chain_node",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("plat_tenant.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("name", sa.String(512), nullable=False, index=True),
        sa.Column("part_number", sa.String(200), nullable=True),
        sa.Column("node_type", sa.String(50), nullable=False, server_default="component"),
        sa.Column("country_of_origin", sa.String(3), nullable=True),
        sa.Column("is_us_origin", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("hs_code", sa.String(20), nullable=True),
        sa.Column("eccn", sa.String(20), nullable=True),
        sa.Column("unit_value_usd", sa.Float(), nullable=True),
        sa.Column("us_controlled_value_usd", sa.Float(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("extra", postgresql.JSONB(), nullable=True),
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

    op.create_table(
        "plat_supply_chain_edge",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "parent_node_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("plat_supply_chain_node.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "child_node_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("plat_supply_chain_node.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("quantity", sa.Float(), nullable=False, server_default="1.0"),
        sa.Column("unit", sa.String(20), nullable=False, server_default="each"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_table("plat_supply_chain_edge")
    op.drop_table("plat_supply_chain_node")
