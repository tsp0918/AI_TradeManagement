"""add org_id to transactions

Revision ID: g4h5i6j7k8l9
Revises: f3a7e2d1c0b9
Create Date: 2026-05-08 18:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = 'g4h5i6j7k8l9'
down_revision = 'd1e2f3a4b5c6'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("transactions") as batch_op:
        batch_op.add_column(sa.Column("org_id", sa.String(36), nullable=True))
        batch_op.create_index("ix_transactions_org_id", ["org_id"])


def downgrade() -> None:
    with op.batch_alter_table("transactions") as batch_op:
        batch_op.drop_index("ix_transactions_org_id")
        batch_op.drop_column("org_id")
