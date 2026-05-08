"""Phase 5: FtaAgreement.origin_country + RegulatoryChange.relevant_org_ids

Revision ID: h6i7j8k9l0m1
Revises: g5h6i7j8k9l0
Create Date: 2026-05-08 20:00:00.000000
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = 'h6i7j8k9l0m1'
down_revision = 'g5h6i7j8k9l0'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "plat_fta_agreement",
        sa.Column("origin_country", sa.String(4), nullable=False, server_default="JP"),
    )
    op.create_index(
        "ix_plat_fta_agreement_origin_country",
        "plat_fta_agreement",
        ["origin_country"],
    )

    op.add_column(
        "regulatory_changes",
        sa.Column("relevant_org_ids", JSONB, nullable=True),
    )


def downgrade() -> None:
    op.drop_column("regulatory_changes", "relevant_org_ids")

    op.drop_index(
        "ix_plat_fta_agreement_origin_country",
        table_name="plat_fta_agreement",
    )
    op.drop_column("plat_fta_agreement", "origin_country")
