"""add_transaction_id_to_scr_screening_result

Revision ID: d56214c5e744
Revises: 9c18968c2899
Create Date: 2026-06-27

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'd56214c5e744'
down_revision: Union[str, None] = '9c18968c2899'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'scr_screening_result',
        sa.Column('transaction_id', sa.Integer(), nullable=True),
    )
    op.create_index(
        'ix_scr_screening_result_transaction_id',
        'scr_screening_result',
        ['transaction_id'],
    )


def downgrade() -> None:
    op.drop_index('ix_scr_screening_result_transaction_id', table_name='scr_screening_result')
    op.drop_column('scr_screening_result', 'transaction_id')
