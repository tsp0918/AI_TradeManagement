"""add transaction lineage fields (source_module, parent_transaction_id, rnd_case_id)

Revision ID: b7e8f9a0b1c2
Revises: a1b2c3d4e5f6
Create Date: 2026-02-28 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b7e8f9a0b1c2"
down_revision: Union[str, None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("transactions", sa.Column("source_module", sa.String(32), nullable=True))
    op.add_column("transactions", sa.Column("parent_transaction_id", sa.Integer, nullable=True))
    op.add_column("transactions", sa.Column("rnd_case_id", sa.String(64), nullable=True))
    # SQLite は名前付き外部キー制約を後付けできないため、create_foreign_key は省略
    # アプリ側で整合性を担保する


def downgrade() -> None:
    op.drop_column("transactions", "rnd_case_id")
    op.drop_column("transactions", "parent_transaction_id")
    op.drop_column("transactions", "source_module")
