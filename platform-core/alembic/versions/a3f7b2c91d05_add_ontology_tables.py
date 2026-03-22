"""add ontology tables (hantei_kubanbang, hantei_threshold, agent_session)

Revision ID: a3f7b2c91d05
Revises: 94d4a6045d94
Create Date: 2026-03-19

NeuroSymbolic オントロジー層の3テーブルを追加する。

    plat_hantei_kubanbang  - 判定項番マスタ（HanteiKubanbang の永続化）
    plat_hantei_threshold  - 閾値定義（1対多）
    plat_agent_session     - エージェント対話セッション
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "a3f7b2c91d05"
down_revision = "94d4a6045d94"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── plat_hantei_kubanbang ─────────────────────────────────────────────
    op.create_table(
        "plat_hantei_kubanbang",
        sa.Column("id",          sa.Integer(),     nullable=False),
        sa.Column("item_no",     sa.String(32),    nullable=False, server_default=""),
        sa.Column("source_type", sa.String(32),    nullable=False),
        sa.Column("article_no",  sa.String(32),    nullable=False, server_default=""),
        sa.Column("eccn",        sa.String(32),    nullable=False, server_default=""),
        sa.Column("title",       sa.String(255),   nullable=False, server_default=""),
        sa.Column("short_label", sa.String(128),   nullable=False, server_default=""),
        sa.Column("concern_level", sa.String(16),  nullable=False, server_default="medium"),
        sa.Column("layer_a_faiss_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("is_active",   sa.Boolean(),     nullable=False, server_default=sa.true()),
        sa.Column("created_at",  sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at",  sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_plat_hantei_kubanbang_item_no",     "plat_hantei_kubanbang", ["item_no"])
    op.create_index("ix_plat_hantei_kubanbang_source_type", "plat_hantei_kubanbang", ["source_type"])
    op.create_index("ix_plat_hantei_kubanbang_eccn",        "plat_hantei_kubanbang", ["eccn"])

    # ── plat_hantei_threshold ─────────────────────────────────────────────
    op.create_table(
        "plat_hantei_threshold",
        sa.Column("id",               sa.Integer(),  nullable=False),
        sa.Column("kubanbang_id",      sa.Integer(),  nullable=False),
        sa.Column("threshold_type",    sa.String(16), nullable=False),
        sa.Column("attr_key",          sa.String(64), nullable=False),
        sa.Column("label",             sa.String(128), nullable=False),
        sa.Column("unit",              sa.String(32),  nullable=True),
        sa.Column("operator",          sa.String(8),   nullable=True),
        sa.Column("controlled_value",  sa.Float(),     nullable=True),
        sa.Column("controlled_values", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("excluded_values",   postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("description",       sa.Text(),      nullable=False, server_default=""),
        sa.Column("priority",          sa.Integer(),   nullable=False, server_default="50"),
        sa.Column("example",           sa.String(128), nullable=True),
        sa.ForeignKeyConstraint(
            ["kubanbang_id"], ["plat_hantei_kubanbang.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_plat_hantei_threshold_kubanbang_id", "plat_hantei_threshold", ["kubanbang_id"])
    op.create_index("ix_plat_hantei_threshold_attr_key",     "plat_hantei_threshold", ["attr_key"])

    # ── plat_agent_session ────────────────────────────────────────────────
    op.create_table(
        "plat_agent_session",
        sa.Column("id",                  sa.Integer(),    nullable=False),
        sa.Column("session_id",          sa.String(64),   nullable=False),
        sa.Column("domain",              sa.String(32),   nullable=False, server_default="hantei"),
        sa.Column("transaction_id",      sa.Integer(),    nullable=True),
        sa.Column("context_snapshot",    postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("candidate_domain_ids", sa.Text(),      nullable=True),
        sa.Column("status",              sa.String(32),   nullable=False, server_default="active"),
        sa.Column("turn_count",          sa.Integer(),    nullable=False, server_default="0"),
        sa.Column("created_at",          sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at",          sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_plat_agent_session_session_id",     "plat_agent_session", ["session_id"], unique=True)
    op.create_index("ix_plat_agent_session_transaction_id", "plat_agent_session", ["transaction_id"])


def downgrade() -> None:
    op.drop_table("plat_agent_session")
    op.drop_table("plat_hantei_threshold")
    op.drop_table("plat_hantei_kubanbang")
