"""add plat_import_profile table

Revision ID: i7j8k9l0m1n2
Revises: h6i7j8k9l0m1
Create Date: 2026-05-24

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = 'i7j8k9l0m1n2'
down_revision = 'h6i7j8k9l0m1'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'plat_import_profile',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        # 品目参照
        sa.Column('product_code', sa.String(100), nullable=False),
        sa.Column('product_name', sa.String(255), nullable=True),
        # 輸入種別
        sa.Column('import_type', sa.String(20), nullable=False, server_default='purchase'),
        # 輸出者・仕入先
        sa.Column('exporter_name', sa.String(255), nullable=True),
        sa.Column('exporter_country', sa.String(4), nullable=True),
        # 輸入先国
        sa.Column('import_country', sa.String(4), nullable=False, server_default='JP'),
        # HS・税関
        sa.Column('hs_code_import', sa.String(12), nullable=True),
        sa.Column('customs_value_usd', sa.Float(), nullable=True),
        sa.Column('currency', sa.String(10), nullable=False, server_default='USD'),
        sa.Column('import_quantity', sa.Float(), nullable=True),
        sa.Column('import_unit', sa.String(20), nullable=True),
        # 輸入許可
        sa.Column('import_license_required', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('import_license_no', sa.String(100), nullable=True),
        sa.Column('import_license_expiry', sa.DateTime(), nullable=True),
        # FTA
        sa.Column('fta_applicable', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('fta_agreement_code', sa.String(50), nullable=True),
        sa.Column('preferential_rate_pct', sa.Float(), nullable=True),
        sa.Column('co_status', sa.String(20), nullable=False, server_default='not_required'),
        # EAR
        sa.Column('eccn_claimed', sa.String(32), nullable=True),
        sa.Column('us_reexport_applicable', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('ear_license_exception', sa.String(50), nullable=True),
        # 輸入規制チェック
        sa.Column('import_restrictions_checked', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('import_restrictions_result', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        # 運用
        sa.Column('last_imported_at', sa.DateTime(), nullable=True),
        sa.Column('import_frequency', sa.String(20), nullable=True),
        sa.Column('supplier_attestation_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('org_id', sa.String(36), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        # メタ
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
    )
    op.create_index('ix_plat_import_profile_product_code', 'plat_import_profile', ['product_code'])
    op.create_index('ix_plat_import_profile_import_type', 'plat_import_profile', ['import_type'])
    op.create_index('ix_plat_import_profile_exporter_country', 'plat_import_profile', ['exporter_country'])
    op.create_index('ix_plat_import_profile_org_id', 'plat_import_profile', ['org_id'])
    op.create_index('ix_plat_import_profile_fta_agreement_code', 'plat_import_profile', ['fta_agreement_code'])


def downgrade() -> None:
    op.drop_index('ix_plat_import_profile_fta_agreement_code', table_name='plat_import_profile')
    op.drop_index('ix_plat_import_profile_org_id', table_name='plat_import_profile')
    op.drop_index('ix_plat_import_profile_exporter_country', table_name='plat_import_profile')
    op.drop_index('ix_plat_import_profile_import_type', table_name='plat_import_profile')
    op.drop_index('ix_plat_import_profile_product_code', table_name='plat_import_profile')
    op.drop_table('plat_import_profile')
