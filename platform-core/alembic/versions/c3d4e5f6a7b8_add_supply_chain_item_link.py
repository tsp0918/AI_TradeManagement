"""add supply_chain item_id link

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-04-26
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "c3d4e5f6a7b8"
down_revision = "b2c3d4e5f6a7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "plat_supply_chain_node",
        sa.Column("item_id", UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_supply_chain_node_item",
        "plat_supply_chain_node",
        "plat_item",
        ["item_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_supply_chain_node_item_id",
        "plat_supply_chain_node",
        ["item_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_supply_chain_node_item_id", table_name="plat_supply_chain_node")
    op.drop_constraint("fk_supply_chain_node_item", "plat_supply_chain_node", type_="foreignkey")
    op.drop_column("plat_supply_chain_node", "item_id")
