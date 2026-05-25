"""add eccn judgment fields to supply chain node

Revision ID: m1n2o3p4q5r6
Revises: l0m1n2o3p4q5
Create Date: 2026-05-24
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "m1n2o3p4q5r6"
down_revision = "l0m1n2o3p4q5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("plat_supply_chain_node",
        sa.Column("eccn_validation_tx_id", sa.Integer(), nullable=True))
    op.add_column("plat_supply_chain_node",
        sa.Column("eccn_judgment_status", sa.String(32), nullable=True))
    op.add_column("plat_supply_chain_node",
        sa.Column("eccn_source", sa.String(32), nullable=True))
    op.add_column("plat_supply_chain_node",
        sa.Column("judgment_evidence", JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("plat_supply_chain_node", "judgment_evidence")
    op.drop_column("plat_supply_chain_node", "eccn_source")
    op.drop_column("plat_supply_chain_node", "eccn_judgment_status")
    op.drop_column("plat_supply_chain_node", "eccn_validation_tx_id")
