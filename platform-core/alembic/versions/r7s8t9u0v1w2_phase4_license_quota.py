"""Phase 4: License Quota — el_license_quota / el_license_allocation

Revision ID: r7s8t9u0v1w2
Revises: q5r6s7t8u9v0
Create Date: 2026-08-15
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "r7s8t9u0v1w2"
down_revision = "q5r6s7t8u9v0"
branch_labels = None
depends_on = None

TIMESTAMPTZ = sa.DateTime(timezone=True)
UUID = postgresql.UUID(as_uuid=True)


def upgrade() -> None:
    # ── el_license_quota ────────────────────────────────────────────────────
    op.create_table(
        "el_license_quota",
        sa.Column("id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("license_no", sa.String(64), nullable=False, unique=True),
        sa.Column("license_type", sa.String(20), nullable=True),   # EAR | FEFTA | individual
        sa.Column("product_code", sa.String(64), nullable=True, index=True),
        sa.Column("eccn", sa.String(20), nullable=True),
        sa.Column("destination_country", sa.CHAR(2), nullable=True),
        sa.Column("total_value_usd", sa.Numeric(18, 2), nullable=True),
        sa.Column("consumed_value_usd", sa.Numeric(18, 2), server_default="0", nullable=False),
        sa.Column("total_unit", sa.Integer, nullable=True),
        sa.Column("consumed_unit", sa.Integer, server_default="0", nullable=False),
        sa.Column("valid_from", sa.Date, nullable=True),
        sa.Column("valid_until", sa.Date, nullable=True),
        sa.Column("status", sa.String(20), server_default="'active'", nullable=False),
        sa.Column("application_id", UUID, nullable=True),
        sa.Column("created_at", TIMESTAMPTZ, server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", TIMESTAMPTZ, server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_el_license_quota_product", "el_license_quota", ["product_code"])
    op.create_index("ix_el_license_quota_status", "el_license_quota", ["status"])

    # ── el_license_allocation ───────────────────────────────────────────────
    op.create_table(
        "el_license_allocation",
        sa.Column("id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("allocation_no", sa.String(20), nullable=False, unique=True),
        sa.Column("quota_id", UUID, sa.ForeignKey("el_license_quota.id", ondelete="CASCADE"),
                  nullable=False, index=True),
        sa.Column("transaction_id", sa.String(64), nullable=True, index=True),
        sa.Column("case_no", sa.String(64), nullable=True),
        sa.Column("product_code", sa.String(64), nullable=True),
        sa.Column("quantity", sa.Numeric(18, 3), nullable=True),
        sa.Column("amount_usd", sa.Numeric(18, 2), nullable=True),
        sa.Column("status", sa.String(20), server_default="'allocated'", nullable=False),
        sa.Column("valid_until", sa.Date, nullable=True),
        sa.Column("allocated_at", TIMESTAMPTZ, server_default=sa.text("now()"), nullable=False),
        sa.Column("consumed_at", TIMESTAMPTZ, nullable=True),
        sa.Column("released_at", TIMESTAMPTZ, nullable=True),
        sa.CheckConstraint("amount_usd > 0 OR amount_usd IS NULL", name="chk_el_alloc_positive_value"),
    )
    op.create_index("ix_el_license_allocation_status", "el_license_allocation", ["status"])


def downgrade() -> None:
    op.drop_table("el_license_allocation")
    op.drop_table("el_license_quota")
