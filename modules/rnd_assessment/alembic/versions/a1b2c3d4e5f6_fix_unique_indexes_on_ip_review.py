"""fix unique indexes on ip_review tables

Revision ID: a1b2c3d4e5f6
Revises: 9a1f2c3d4e55
Create Date: 2026-02-22 00:00:00.000000

"""

from alembic import op


revision = "a1b2c3d4e5f6"
down_revision = "9a1f2c3d4e55"
branch_labels = None
depends_on = None


def upgrade():
    op.drop_index("ix_rd_ip_reviews_profile_id", table_name="rd_ip_reviews")
    op.create_index(op.f("ix_rd_ip_reviews_profile_id"), "rd_ip_reviews", ["profile_id"], unique=True)

    op.drop_index("ix_rd_ip_review_evidence_stored_filename", table_name="rd_ip_review_evidence")
    op.create_index(op.f("ix_rd_ip_review_evidence_stored_filename"), "rd_ip_review_evidence", ["stored_filename"], unique=True)


def downgrade():
    op.drop_index(op.f("ix_rd_ip_review_evidence_stored_filename"), table_name="rd_ip_review_evidence")
    op.create_index("ix_rd_ip_review_evidence_stored_filename", "rd_ip_review_evidence", ["stored_filename"], unique=False)

    op.drop_index(op.f("ix_rd_ip_reviews_profile_id"), table_name="rd_ip_reviews")
    op.create_index("ix_rd_ip_reviews_profile_id", "rd_ip_reviews", ["profile_id"], unique=False)
