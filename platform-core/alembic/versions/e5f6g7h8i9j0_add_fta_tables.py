"""add fta tables

Revision ID: e5f6g7h8i9j0
Revises: d1e2f3a4b5c6
Create Date: 2026-05-07

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = 'e5f6g7h8i9j0'
down_revision: Union[str, None] = 'd1e2f3a4b5c6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'plat_fta_agreement',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('code', sa.String(50), nullable=False),
        sa.Column('name_ja', sa.String(200), nullable=False),
        sa.Column('name_en', sa.String(200), nullable=False),
        sa.Column('partner_countries', sa.Text(), nullable=False),
        sa.Column('effective_date', sa.String(20), nullable=True),
        sa.Column('status', sa.String(20), nullable=False, server_default='active'),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('code'),
    )
    op.create_index('ix_plat_fta_agreement_code', 'plat_fta_agreement', ['code'])

    op.create_table(
        'plat_fta_rate',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('agreement_code', sa.String(50), nullable=False),
        sa.Column('hs_code', sa.String(20), nullable=False),
        sa.Column('hs_description', sa.String(500), nullable=True),
        sa.Column('year', sa.Integer(), nullable=False),
        sa.Column('mfn_rate_pct', sa.Float(), nullable=True),
        sa.Column('preferential_rate_pct', sa.Float(), nullable=True),
        sa.Column('staging_category', sa.String(20), nullable=True),
        sa.Column('is_eliminated', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('elimination_year', sa.Integer(), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('agreement_code', 'hs_code', 'year', name='uq_fta_rate'),
    )
    op.create_index('ix_plat_fta_rate_agreement_code', 'plat_fta_rate', ['agreement_code'])
    op.create_index('ix_plat_fta_rate_hs_code', 'plat_fta_rate', ['hs_code'])


def downgrade() -> None:
    op.drop_table('plat_fta_rate')
    op.drop_table('plat_fta_agreement')
