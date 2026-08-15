"""add personnel table and rnd_access_logs table

Revision ID: e7f8a9b0c1d2
Revises: d6e7f8a9b0c1
Create Date: 2026-07-27 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa

revision = "e7f8a9b0c1d2"
down_revision = "d6e7f8a9b0c1"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "personnel",
        sa.Column("personnel_id", sa.String(36), primary_key=True),
        sa.Column("case_id", sa.String(64), sa.ForeignKey("rd_cases.case_id", ondelete="SET NULL"), index=True, nullable=True),
        sa.Column("tenant_id", sa.String(64), index=True, nullable=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("role", sa.String(100), nullable=True),
        sa.Column("affiliation", sa.String(200), nullable=True),
        sa.Column("nationality", sa.String(2), nullable=True),
        sa.Column("residence_country", sa.String(2), nullable=True),
        sa.Column("years_in_japan", sa.Float(), nullable=True),
        sa.Column("dual_employment_flag", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("dual_employer_name", sa.String(200), nullable=True),
        sa.Column("dual_employer_country", sa.String(2), nullable=True),
        sa.Column("tech_access_eccn", sa.String(100), nullable=True),
        sa.Column("tech_access_fefta", sa.String(100), nullable=True),
        sa.Column("deemed_export_category", sa.String(1), nullable=True),
        sa.Column("deemed_export_risk", sa.String(20), nullable=True),
        sa.Column("deemed_export_reason", sa.Text(), nullable=True),
        sa.Column("screened_at", sa.DateTime(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_personnel_case_id", "personnel", ["case_id"])
    op.create_index("ix_personnel_tenant_id", "personnel", ["tenant_id"])
    op.create_index("ix_personnel_nationality", "personnel", ["nationality"])

    op.create_table(
        "rnd_access_logs",
        sa.Column("log_id", sa.String(), primary_key=True),
        sa.Column("case_id", sa.String(), sa.ForeignKey("rd_cases.case_id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("user_id", sa.String(), nullable=True, index=True),
        sa.Column("org_id", sa.String(), nullable=True, index=True),
        sa.Column("action", sa.String(40), nullable=False, server_default="view"),
        sa.Column("sensitivity_at_access", sa.String(20), nullable=True),
        sa.Column("deemed_export_flagged", sa.String(5), nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime(), nullable=False, index=True),
    )


def downgrade():
    op.drop_table("rnd_access_logs")
    op.drop_index("ix_personnel_nationality", table_name="personnel")
    op.drop_index("ix_personnel_tenant_id", table_name="personnel")
    op.drop_index("ix_personnel_case_id", table_name="personnel")
    op.drop_table("personnel")
