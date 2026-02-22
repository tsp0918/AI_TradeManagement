"""add ip review and evidence

Revision ID: 9a1f2c3d4e55
Revises: 8f2a3c9b0d11
Create Date: 2026-02-21 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "9a1f2c3d4e55"
down_revision = "8f2a3c9b0d11"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "rd_ip_reviews",
        sa.Column("ip_review_id", sa.String(), primary_key=True),
        sa.Column("profile_id", sa.String(), sa.ForeignKey("rd_case_profiles.profile_id"), nullable=False, unique=True),
        sa.Column("status", sa.String(), nullable=False, server_default="draft"),
        sa.Column("reviewer_user_id", sa.String(), nullable=True),
        sa.Column("reviewer_comment", sa.Text(), nullable=True),
        sa.Column("finalized_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_rd_ip_reviews_profile_id", "rd_ip_reviews", ["profile_id"], unique=True)
    op.create_index("ix_rd_ip_reviews_status", "rd_ip_reviews", ["status"])

    op.create_table(
        "rd_ip_review_evidence",
        sa.Column("evidence_id", sa.String(), primary_key=True),
        sa.Column("ip_review_id", sa.String(), sa.ForeignKey("rd_ip_reviews.ip_review_id"), nullable=False),
        sa.Column("original_filename", sa.String(), nullable=False),
        sa.Column("stored_filename", sa.String(), nullable=False, unique=True),
        sa.Column("content_type", sa.String(), nullable=True),
        sa.Column("size_bytes", sa.Integer(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_rd_ip_review_evidence_ip_review_id", "rd_ip_review_evidence", ["ip_review_id"])
    op.create_index("ix_rd_ip_review_evidence_stored_filename", "rd_ip_review_evidence", ["stored_filename"], unique=True)


def downgrade():
    op.drop_index("ix_rd_ip_review_evidence_stored_filename", table_name="rd_ip_review_evidence")
    op.drop_index("ix_rd_ip_review_evidence_ip_review_id", table_name="rd_ip_review_evidence")
    op.drop_table("rd_ip_review_evidence")

    op.drop_index("ix_rd_ip_reviews_status", table_name="rd_ip_reviews")
    op.drop_index("ix_rd_ip_reviews_profile_id", table_name="rd_ip_reviews")
    op.drop_table("rd_ip_reviews")