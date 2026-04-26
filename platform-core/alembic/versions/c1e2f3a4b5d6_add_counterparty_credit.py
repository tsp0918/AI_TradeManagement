"""add counterparty credit management columns and table

Revision ID: c1e2f3a4b5d6
Revises: b5e2f8a01c77
Create Date: 2026-04-26

与信管理フィールドを plat_company に追加し、与信スコア変更履歴テーブルを新設する。
  plat_company                  - roles / credit_score / credit_data / country_risk_score / overall_risk_level 追加
  plat_counterparty_credit_history - 与信スコア変更履歴（新規）
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "c1e2f3a4b5d6"
down_revision = "b5e2f8a01c77"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # plat_company に与信管理カラム追加
    op.add_column("plat_company", sa.Column("roles", postgresql.JSONB(), nullable=True))
    op.add_column("plat_company", sa.Column("credit_score", sa.Integer(), nullable=True))
    op.add_column("plat_company", sa.Column("credit_data", postgresql.JSONB(), nullable=True))
    op.add_column("plat_company", sa.Column("country_risk_score", sa.Integer(), nullable=True))
    op.add_column("plat_company", sa.Column("overall_risk_level", sa.String(20), nullable=True))

    # 与信スコア変更履歴テーブル新設
    op.create_table(
        "plat_counterparty_credit_history",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "company_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("plat_company.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("changed_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("previous_credit_score", sa.Integer(), nullable=True),
        sa.Column("new_credit_score", sa.Integer(), nullable=True),
        sa.Column("previous_risk_level", sa.String(20), nullable=True),
        sa.Column("new_risk_level", sa.String(20), nullable=True),
        sa.Column("change_reason", sa.Text(), nullable=True),
        sa.Column("change_detail", postgresql.JSONB(), nullable=True),
        sa.Column(
            "changed_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_table("plat_counterparty_credit_history")
    for col in ("overall_risk_level", "country_risk_score", "credit_data", "credit_score", "roles"):
        op.drop_column("plat_company", col)
