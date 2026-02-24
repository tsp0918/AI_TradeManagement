"""add counterparty screening fields

Revision ID: a1b2c3d4e5f6
Revises: 16734207caef
Create Date: 2026-02-24 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, None] = '16734207caef'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('transactions', sa.Column('counterparty_name', sa.String(255), nullable=True))
    op.add_column('transactions', sa.Column('screening_result_id', sa.String(36), nullable=True))
    op.add_column('transactions', sa.Column('screening_status', sa.String(30), nullable=True))


def downgrade() -> None:
    op.drop_column('transactions', 'screening_status')
    op.drop_column('transactions', 'screening_result_id')
    op.drop_column('transactions', 'counterparty_name')
