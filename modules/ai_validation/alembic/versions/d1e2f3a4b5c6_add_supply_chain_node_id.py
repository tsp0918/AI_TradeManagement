"""add supply_chain_node_id to transactions

Revision ID: d1e2f3a4b5c6
Revises: c1d2e3f4a5b6
Create Date: 2026-04-26

transactions テーブルに supply_chain_node_id を追加する。
platform-core の plat_supply_chain_node と論理的に紐付け、
De Minimis 計算結果を取引審査に反映できるようにする。
（外部 DB への FK は持たず、UUID 文字列として保持）
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "d1e2f3a4b5c6"
down_revision: str = "c1d2e3f4a5b6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "transactions",
        sa.Column("supply_chain_node_id", sa.String(36), nullable=True),
    )
    # De Minimis 計算結果キャッシュ（取引審査時点のスナップショット）
    op.add_column(
        "transactions",
        sa.Column("de_minimis_result", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("transactions", "de_minimis_result")
    op.drop_column("transactions", "supply_chain_node_id")
