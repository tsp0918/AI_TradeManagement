"""add plat_hantei_records table for cross-module 該非判定 data lineage

Revision ID: o3p4q5r6s7t8
Revises: n2o3p4q5r6s7
Create Date: 2026-08-02
"""
from alembic import op
import sqlalchemy as sa

revision = "o3p4q5r6s7t8"
down_revision = "n2o3p4q5r6s7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "plat_hantei_records",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        # 品目コード — 全モジュールの共通キー
        # rnd_assessment: "RND-{case_id}", ai_classification: product.code, ai_validation: linked_product_code
        sa.Column("product_code", sa.String(128), nullable=False, index=True),
        # ソースモジュール
        sa.Column("source_module", sa.String(32), nullable=False),  # rnd | classification | validation
        # 関連ID（モジュール内部参照用）
        sa.Column("rnd_case_id", sa.String(64), nullable=True),
        sa.Column("transaction_id", sa.Integer(), nullable=True),
        # 規制項番情報
        sa.Column("item_no", sa.String(64), nullable=False, default=""),
        sa.Column("item_label", sa.String(256), nullable=False, default=""),
        sa.Column("source_type", sa.String(64), nullable=False, default=""),
        sa.Column("regulation_text", sa.Text(), nullable=True),
        # Ollama LLM 判定結果
        sa.Column("llm_verdict", sa.String(32), nullable=False, default=""),   # APPLICABLE/REVIEW_NEEDED/NOT_APPLICABLE
        sa.Column("llm_confidence", sa.String(16), nullable=False, default=""), # HIGH/MEDIUM/LOW
        sa.Column("llm_reason", sa.Text(), nullable=True),
        sa.Column("llm_key_question", sa.Text(), nullable=True),
        # 転記済み判定
        sa.Column("decision", sa.String(32), nullable=False, default=""),  # controlled/needs_review/non_controlled
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("recorded_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_hantei_records_product_code", "plat_hantei_records", ["product_code"])
    op.create_index("ix_hantei_records_source_module", "plat_hantei_records", ["source_module"])


def downgrade() -> None:
    op.drop_index("ix_hantei_records_source_module")
    op.drop_index("ix_hantei_records_product_code")
    op.drop_table("plat_hantei_records")
