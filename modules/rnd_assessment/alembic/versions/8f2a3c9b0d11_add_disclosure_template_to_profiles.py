"""add disclosure template to profiles

Revision ID: 8f2a3c9b0d11
Revises: dc39da4eb83b
Create Date: 2026-02-21 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "8f2a3c9b0d11"
down_revision = "dc39da4eb83b"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("rd_case_profiles") as batch_op:
        batch_op.add_column(sa.Column("disclosure_requirements_raw", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("disclosure_template_json", sa.JSON(), nullable=True))


def downgrade():
    with op.batch_alter_table("rd_case_profiles") as batch_op:
        batch_op.drop_column("disclosure_template_json")
        batch_op.drop_column("disclosure_requirements_raw")