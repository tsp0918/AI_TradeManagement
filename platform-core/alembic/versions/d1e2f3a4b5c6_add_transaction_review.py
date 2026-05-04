"""add transaction_review table

Revision ID: d1e2f3a4b5c6
Revises: c1e2f3a4b5d6
Create Date: 2026-05-03

ERP 取引伝票・出荷伝票と AI 該非判定を紐づける取引審査テーブルを新設する。
  plat_transaction_review  - 取引審査レコード（新規）
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "d1e2f3a4b5c6"
down_revision = "c3d4e5f6a7b8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "plat_transaction_review",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("erp_transaction_id", sa.String(100), nullable=True),
        sa.Column("erp_shipment_id", sa.String(100), nullable=True),
        sa.Column("item_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("item_code", sa.String(100), nullable=True),
        sa.Column("item_name", sa.String(512), nullable=True),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("company_name", sa.String(512), nullable=True),
        sa.Column("destination_country", sa.String(10), nullable=True),
        sa.Column("eccn", sa.String(32), nullable=True),
        sa.Column("judgment", sa.String(20), nullable=False, server_default="PENDING"),
        sa.Column("review_level", sa.String(10), nullable=False, server_default="AUTO"),
        sa.Column("review_completed", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("approved", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("screening_snapshot", postgresql.JSONB(), nullable=True),
        sa.Column("rescreen_result", postgresql.JSONB(), nullable=True),
        sa.Column("rescreen_changed", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("reviewed_by", sa.String(200), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_ptr_erp_transaction_id", "plat_transaction_review", ["erp_transaction_id"])
    op.create_index("ix_ptr_erp_shipment_id",    "plat_transaction_review", ["erp_shipment_id"])
    op.create_index("ix_ptr_item_id",            "plat_transaction_review", ["item_id"])
    op.create_index("ix_ptr_company_id",         "plat_transaction_review", ["company_id"])


def downgrade() -> None:
    op.drop_table("plat_transaction_review")
