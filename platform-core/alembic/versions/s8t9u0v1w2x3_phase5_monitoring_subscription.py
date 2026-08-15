"""phase5_monitoring_subscription

Revision ID: s8t9u0v1w2x3
Revises: r7s8t9u0v1w2
Create Date: 2026-08-16
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "s8t9u0v1w2x3"
down_revision = "r7s8t9u0v1w2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "monitoring_subscription",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("subject_type", sa.String(20), nullable=False),   # 'party' | 'transaction'
        sa.Column("subject_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("trigger_type", sa.String(30), nullable=False),   # 'sanction_change' | 'contract_end'
        sa.Column("monitor_until", sa.Date, nullable=True),
        sa.Column("is_active", sa.Boolean, server_default=sa.text("true"), nullable=False),
        sa.Column("created_from_if", sa.String(10), nullable=True),  # 'IF-01' | 'IF-02'
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
    )
    # アクティブなサブスクリプションのユニーク制約（同一対象 × トリガー種別は1件のみ）
    op.create_index(
        "uq_monitoring_subject_trigger",
        "monitoring_subscription",
        ["subject_type", "subject_id", "trigger_type"],
        unique=True,
        postgresql_where=sa.text("is_active = TRUE"),
    )
    op.create_index(
        "ix_monitoring_active_until",
        "monitoring_subscription",
        ["is_active", "monitor_until"],
    )


def downgrade() -> None:
    op.drop_index("ix_monitoring_active_until", table_name="monitoring_subscription")
    op.drop_index("uq_monitoring_subject_trigger", table_name="monitoring_subscription")
    op.drop_table("monitoring_subscription")
