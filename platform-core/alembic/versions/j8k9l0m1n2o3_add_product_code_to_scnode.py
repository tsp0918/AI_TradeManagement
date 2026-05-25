"""add product_code to plat_supply_chain_node

Revision ID: j8k9l0m1n2o3
Revises: i7j8k9l0m1n2
Create Date: 2026-05-24

BOM統合 Phase I-2: ai_classification 品目コードとの soft FK。
"""
from alembic import op
import sqlalchemy as sa

revision = "j8k9l0m1n2o3"
down_revision = "i7j8k9l0m1n2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "plat_supply_chain_node",
        sa.Column("product_code", sa.String(100), nullable=True),
    )
    op.create_index(
        "ix_plat_supply_chain_node_product_code",
        "plat_supply_chain_node",
        ["product_code"],
    )


def downgrade() -> None:
    op.drop_index("ix_plat_supply_chain_node_product_code", "plat_supply_chain_node")
    op.drop_column("plat_supply_chain_node", "product_code")
