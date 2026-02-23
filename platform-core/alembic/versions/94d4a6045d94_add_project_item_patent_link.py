"""add_project_item_patent_link

Revision ID: 94d4a6045d94
Revises: 081b20be6eae
Create Date: 2026-02-23 13:14:59.463579

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '94d4a6045d94'
down_revision: Union[str, None] = '081b20be6eae'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'plat_item',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('item_code', sa.String(length=100), nullable=True),
        sa.Column('name', sa.String(length=512), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('hs_code', sa.String(length=20), nullable=True),
        sa.Column('eccn', sa.String(length=20), nullable=True),
        sa.Column('export_control_status', sa.String(length=50), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_plat_item_eccn'), 'plat_item', ['eccn'], unique=False)
    op.create_index(op.f('ix_plat_item_hs_code'), 'plat_item', ['hs_code'], unique=False)
    op.create_index(op.f('ix_plat_item_item_code'), 'plat_item', ['item_code'], unique=True)

    op.create_table(
        'plat_project',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('code', sa.String(length=50), nullable=True),
        sa.Column('title', sa.String(length=512), nullable=False),
        sa.Column('description', sa.String(length=2048), nullable=True),
        sa.Column('status', sa.String(length=50), nullable=False),
        sa.Column('current_phase', sa.String(length=100), nullable=True),
        sa.Column('merged_into', sa.UUID(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['merged_into'], ['plat_project.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_plat_project_code'), 'plat_project', ['code'], unique=True)
    op.create_index(op.f('ix_plat_project_status'), 'plat_project', ['status'], unique=False)

    op.create_table(
        'plat_project_patent_link',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('project_id', sa.UUID(), nullable=False),
        sa.Column('patent_publication_number', sa.String(length=100), nullable=False),
        sa.Column('note', sa.Text(), nullable=True),
        sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['project_id'], ['plat_project.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        op.f('ix_plat_project_patent_link_patent_publication_number'),
        'plat_project_patent_link', ['patent_publication_number'], unique=False,
    )
    op.create_index(
        op.f('ix_plat_project_patent_link_project_id'),
        'plat_project_patent_link', ['project_id'], unique=False,
    )
    op.create_index(
        'uq_project_patent_active',
        'plat_project_patent_link', ['project_id', 'patent_publication_number'],
        unique=True,
        postgresql_where=sa.text('deleted_at IS NULL'),
    )


def downgrade() -> None:
    op.drop_index('uq_project_patent_active', table_name='plat_project_patent_link',
                  postgresql_where=sa.text('deleted_at IS NULL'))
    op.drop_index(op.f('ix_plat_project_patent_link_project_id'), table_name='plat_project_patent_link')
    op.drop_index(op.f('ix_plat_project_patent_link_patent_publication_number'), table_name='plat_project_patent_link')
    op.drop_table('plat_project_patent_link')

    op.drop_index(op.f('ix_plat_project_status'), table_name='plat_project')
    op.drop_index(op.f('ix_plat_project_code'), table_name='plat_project')
    op.drop_table('plat_project')

    op.drop_index(op.f('ix_plat_item_item_code'), table_name='plat_item')
    op.drop_index(op.f('ix_plat_item_hs_code'), table_name='plat_item')
    op.drop_index(op.f('ix_plat_item_eccn'), table_name='plat_item')
    op.drop_table('plat_item')
