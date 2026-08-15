"""add access control fields to rd_cases and promotion fields to rd_case_profiles

Revision ID: d6e7f8a9b0c1
Revises: c5d6e7f8a9b0
Create Date: 2026-07-27 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa

revision = "d6e7f8a9b0c1"
down_revision = "c5d6e7f8a9b0"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("rd_cases") as batch_op:
        batch_op.add_column(sa.Column("tech_sensitivity", sa.String(20), nullable=False, server_default="public"))
        batch_op.add_column(sa.Column("access_org_ids", sa.JSON(), nullable=True))

    with op.batch_alter_table("rd_case_profiles") as batch_op:
        batch_op.add_column(sa.Column("promoted_product_id", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("promoted_at", sa.DateTime(), nullable=True))


def downgrade():
    with op.batch_alter_table("rd_case_profiles") as batch_op:
        batch_op.drop_column("promoted_at")
        batch_op.drop_column("promoted_product_id")

    with op.batch_alter_table("rd_cases") as batch_op:
        batch_op.drop_column("access_org_ids")
        batch_op.drop_column("tech_sensitivity")
