"""init_screening_schema

Revision ID: 9c18968c2899
Revises:
Create Date: 2026-02-22 21:46:48.400807

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = '9c18968c2899'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('scr_watchlist',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('list_source', sa.String(length=50), nullable=False),
    sa.Column('entity_name', sa.String(length=500), nullable=False),
    sa.Column('aliases', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('address', sa.Text(), nullable=True),
    sa.Column('country', sa.String(length=10), nullable=True),
    sa.Column('source_id', sa.String(length=200), nullable=True),
    sa.Column('reason', sa.Text(), nullable=True),
    sa.Column('risk_level', sa.String(length=20), nullable=False),
    sa.Column('extra', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_scr_watchlist_country'), 'scr_watchlist', ['country'], unique=False)
    op.create_index(op.f('ix_scr_watchlist_entity_name'), 'scr_watchlist', ['entity_name'], unique=False)
    op.create_index(op.f('ix_scr_watchlist_is_active'), 'scr_watchlist', ['is_active'], unique=False)
    op.create_index(op.f('ix_scr_watchlist_list_source'), 'scr_watchlist', ['list_source'], unique=False)
    op.create_index(op.f('ix_scr_watchlist_source_id'), 'scr_watchlist', ['source_id'], unique=False)

    op.create_table('scr_screening_result',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('company_id', sa.UUID(), nullable=True),
    sa.Column('query_name', sa.String(length=500), nullable=False),
    sa.Column('query_country', sa.String(length=10), nullable=True),
    sa.Column('query_address', sa.Text(), nullable=True),
    sa.Column('result_status', sa.String(length=30), nullable=False),
    sa.Column('max_score', sa.Float(), nullable=True),
    sa.Column('matches', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('screened_by', sa.UUID(), nullable=True),
    sa.Column('screened_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_scr_screening_result_company_id'), 'scr_screening_result', ['company_id'], unique=False)
    op.create_index(op.f('ix_scr_screening_result_result_status'), 'scr_screening_result', ['result_status'], unique=False)
    op.create_index(op.f('ix_scr_screening_result_screened_at'), 'scr_screening_result', ['screened_at'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_scr_screening_result_screened_at'), table_name='scr_screening_result')
    op.drop_index(op.f('ix_scr_screening_result_result_status'), table_name='scr_screening_result')
    op.drop_index(op.f('ix_scr_screening_result_company_id'), table_name='scr_screening_result')
    op.drop_table('scr_screening_result')
    op.drop_index(op.f('ix_scr_watchlist_source_id'), table_name='scr_watchlist')
    op.drop_index(op.f('ix_scr_watchlist_list_source'), table_name='scr_watchlist')
    op.drop_index(op.f('ix_scr_watchlist_is_active'), table_name='scr_watchlist')
    op.drop_index(op.f('ix_scr_watchlist_entity_name'), table_name='scr_watchlist')
    op.drop_index(op.f('ix_scr_watchlist_country'), table_name='scr_watchlist')
    op.drop_table('scr_watchlist')
