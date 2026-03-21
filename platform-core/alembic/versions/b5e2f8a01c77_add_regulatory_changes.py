"""add regulatory_changes table

Revision ID: b5e2f8a01c77
Revises: a3f7b2c91d05
Create Date: 2026-03-21

規制動向モニタリング結果を永続化するテーブルを追加する。
  regulatory_changes  - e-Gov / BIS Federal Register の変更検知ログ
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "b5e2f8a01c77"
down_revision = "a3f7b2c91d05"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "regulatory_changes",
        sa.Column("id",             sa.Integer(),     nullable=False, autoincrement=True),
        sa.Column("source",         sa.String(50),    nullable=False),
        sa.Column("change_type",    sa.String(50),    nullable=True),
        sa.Column("title",          sa.String(500),   nullable=False),
        sa.Column("description",    sa.Text(),        nullable=True),
        sa.Column("severity",       sa.String(20),    nullable=False, server_default="info"),
        sa.Column("item_ref",       sa.String(200),   nullable=True),
        sa.Column("effective_date", sa.String(20),    nullable=True),
        sa.Column("source_url",     sa.String(1000),  nullable=True),
        sa.Column("content_hash",   sa.String(64),    nullable=False),
        sa.Column("is_dismissed",   sa.Boolean(),     nullable=False, server_default="false"),
        sa.Column("dismissed_at",   sa.DateTime(),    nullable=True),
        sa.Column("detected_at",    sa.DateTime(),    nullable=False),
        sa.Column("created_at",     sa.DateTime(),    nullable=False, server_default=sa.text("NOW()")),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("content_hash", name="uq_regulatory_changes_content_hash"),
    )
    op.create_index("ix_regulatory_changes_source",       "regulatory_changes", ["source"])
    op.create_index("ix_regulatory_changes_content_hash", "regulatory_changes", ["content_hash"])


def downgrade() -> None:
    op.drop_index("ix_regulatory_changes_content_hash", table_name="regulatory_changes")
    op.drop_index("ix_regulatory_changes_source",       table_name="regulatory_changes")
    op.drop_table("regulatory_changes")
