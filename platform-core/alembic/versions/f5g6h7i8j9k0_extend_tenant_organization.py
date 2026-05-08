"""extend tenant with multi-location organization fields

Revision ID: f5g6h7i8j9k0
Revises: e5f6g7h8i9j0
Create Date: 2026-05-08

plat_tenant に多拠点管理フィールドを追加する。
既存データへの影響: なし（全カラム nullable）
起動時自動シード: JP_HQ レコードの更新は platform-core startup で実行。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = 'f5g6h7i8j9k0'
down_revision: Union[str, None] = 'e5f6g7h8i9j0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('plat_tenant', sa.Column('country_code', sa.String(2), nullable=True))
    op.add_column('plat_tenant', sa.Column('export_regime', sa.String(20), nullable=True))
    op.add_column('plat_tenant', sa.Column('org_type', sa.String(20), nullable=True))
    op.add_column('plat_tenant', sa.Column('timezone', sa.String(50), nullable=True))
    op.add_column('plat_tenant', sa.Column('flag_emoji', sa.String(8), nullable=True))
    op.add_column(
        'plat_tenant',
        sa.Column(
            'parent_id',
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey('plat_tenant.id', ondelete='SET NULL'),
            nullable=True,
        ),
    )
    op.create_index('ix_plat_tenant_country_code', 'plat_tenant', ['country_code'])
    op.create_index('ix_plat_tenant_parent_id', 'plat_tenant', ['parent_id'])

    # デフォルトテナントを JP_HQ として初期化
    op.execute("""
        UPDATE plat_tenant
        SET country_code = 'JP',
            export_regime = 'FEFTA',
            org_type      = 'HQ',
            timezone      = 'Asia/Tokyo',
            flag_emoji    = '🇯🇵'
        WHERE slug = 'default'
    """)


def downgrade() -> None:
    op.drop_index('ix_plat_tenant_country_code', table_name='plat_tenant')
    op.drop_index('ix_plat_tenant_parent_id', table_name='plat_tenant')
    op.drop_column('plat_tenant', 'parent_id')
    op.drop_column('plat_tenant', 'flag_emoji')
    op.drop_column('plat_tenant', 'timezone')
    op.drop_column('plat_tenant', 'org_type')
    op.drop_column('plat_tenant', 'export_regime')
    op.drop_column('plat_tenant', 'country_code')
